"""Draft analyst — meta-aware draft reads + a calibration log.

Two jobs:
  1. META FEED: pulls OpenDota /heroStats (live current-patch hero win/pick/ban
     rates) so a draft read is informed by what's actually strong RIGHT NOW, not
     stale meta knowledge. Cached to logs/hero_meta.json (refresh ~daily).
  2. DRAFT TABLE: for a live match, pulls both teams' heroes and annotates each
     with its current meta stats → a structured comparison to reason over.
  3. CALL LOG: `--call <match> <team> <confidence>` logs a draft call to
     logs/draft_calls.csv so reads can be validated vs outcomes over time
     (same discipline as the bot's settlement-shadow log).

Usage:
  python3 draft_analyst.py <match_id>                 # meta-annotated draft table
  python3 draft_analyst.py --refresh                  # force-refresh meta feed
  python3 draft_analyst.py --call <match_id> <team> <lean|strong>   # log a call
  python3 draft_analyst.py --score                    # tally logged calls vs results
"""
from __future__ import annotations
import json, os, sys, time, urllib.request, csv, asyncio, aiohttp

META = "logs/hero_meta.json"
CALLS = "logs/draft_calls.csv"
META_TTL = 24 * 3600  # refresh daily


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    return json.load(urllib.request.urlopen(req, timeout=25))


def refresh_meta(force=False):
    if not force and os.path.exists(META) and (time.time() - os.path.getmtime(META)) < META_TTL:
        return json.load(open(META))
    print("  fetching OpenDota /heroStats (current meta)...", flush=True)
    raw = _get("https://api.opendota.com/api/heroStats")
    meta = {}
    for h in raw:
        name = h.get("localized_name")
        pro_pick = h.get("pro_pick") or 0
        pro_win = h.get("pro_win") or 0
        pro_ban = h.get("pro_ban") or 0
        # high-bracket pub (Divine/Immortal = brackets 7,8)
        hp = (h.get("7_pick") or 0) + (h.get("8_pick") or 0)
        hw = (h.get("7_win") or 0) + (h.get("8_win") or 0)
        meta[name] = {
            "pro_wr": round(pro_win / pro_pick, 3) if pro_pick >= 5 else None,
            "pro_pick": pro_pick, "pro_ban": pro_ban,
            "pro_contested": pro_pick + pro_ban,
            "pub_wr": round(hw / hp, 3) if hp >= 200 else None,
        }
    json.dump(meta, open(META, "w"))
    print(f"  cached {len(meta)} heroes' meta", flush=True)
    return meta


async def _live_heroes(match_id):
    from steam_client import fetch_all_live_games
    async with aiohttp.ClientSession() as s:
        g = await fetch_all_live_games(s, include_league=True)
    m = next((x for x in g if str(x.get("match_id")) == str(match_id)), None)
    if not m:
        return None
    ps = m.get("players", [])
    return {
        "radiant_team": m.get("radiant_team"), "dire_team": m.get("dire_team"),
        "radiant_lead": m.get("radiant_lead"), "game_time": (m.get("game_time_sec") or 0) // 60,
        "rad": [p.get("hero_name") for p in ps if p.get("team") == 0],
        "dire": [p.get("hero_name") for p in ps if p.get("team") == 1],
    }


def _annotate(heroes, meta):
    rows, wr_sum, wr_n, contested = [], 0.0, 0, 0
    for h in heroes:
        m = meta.get(h, {})
        pro = m.get("pro_wr"); pub = m.get("pub_wr"); con = m.get("pro_contested", 0)
        tier = ""
        wr = pro if pro is not None else pub
        if wr is not None:
            wr_sum += wr; wr_n += 1
            tier = "S" if wr >= 0.55 else "A" if wr >= 0.52 else "C" if wr < 0.47 else "B"
        contested += con
        rows.append((h, pro, pub, con, tier))
    avg_wr = wr_sum / wr_n if wr_n else None
    return rows, avg_wr, contested


def show(match_id):
    meta = refresh_meta()
    info = asyncio.run(_live_heroes(match_id))
    if not info:
        print(f"  match {match_id} not in live feed (ended or not live)")
        return
    print(f"\n=== DRAFT: {info['radiant_team']} vs {info['dire_team']}  ({info['game_time']}m, nw(rad)={info['radiant_lead']:+}) ===")
    for side, team in [("rad", info["radiant_team"]), ("dire", info["dire_team"])]:
        rows, avg, con = _annotate(info[side], meta)
        print(f"\n  {team} {'(radiant)' if side=='rad' else '(dire)'}:")
        print(f"    {'hero':<18}{'pro_wr':>8}{'pub_wr':>8}{'contested':>10}{'tier':>6}")
        for h, pro, pub, c, tier in rows:
            pw = f"{pro*100:.0f}%" if pro is not None else "—"
            bw = f"{pub*100:.1f}%" if pub is not None else "—"
            print(f"    {h:<18}{pw:>8}{bw:>8}{c:>10}{tier:>6}")
        print(f"    → avg win-rate: {avg*100:.1f}%" if avg else "    → avg win-rate: n/a", f" | total contested: {con}")
    print("\n  (meta-informed inputs — combine with hero matchups, roles, scaling for the call)")


def _market_odds(match_id, team):
    """Current market ASK for `team` on the bound market = what the MARKET thinks.
    This is the benchmark we must BEAT. Returns (odds, token) or (None, None)."""
    try:
        import requests, yaml
        md = yaml.safe_load(open("markets.yaml"))
        mk = next((m for m in md.get("markets", []) if str(m.get("dota_match_id")) == str(match_id)), None)
        if not mk:
            return None, None
        tl = (team or "").lower()
        tok = mk["yes_token_id"] if tl in (mk.get("yes_team", "") or "").lower() else mk["no_token_id"]
        bk = requests.get(f"https://clob.polymarket.com/book?token_id={tok}", timeout=8).json()
        asks = sorted(float(a["price"]) for a in bk.get("asks", []))
        return (asks[0] if asks else None), tok
    except Exception:
        return None, None


def log_call(match_id, team, confidence):
    odds, _ = _market_odds(match_id, team)
    # vs_market: are we calling the market's FAVORITE (chalk, no edge) or its
    # UNDERDOG (disagreement — the only place real alpha can live)?
    if odds is None:
        vs = "no_market"
    elif odds < 0.50:
        vs = "AGAINST"   # we call a team the market has as underdog
    else:
        vs = "with"      # we agree with the market (chalk)
    new = not os.path.exists(CALLS)
    with open(CALLS, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "match_id", "called_team", "confidence", "market_odds", "vs_market", "result", "correct"])
        from datetime import datetime, timezone
        w.writerow([datetime.now(timezone.utc).isoformat(timespec="seconds"), match_id, team, confidence,
                    odds if odds is not None else "", vs, "PENDING", ""])
    od = f"{odds:.2f}" if odds is not None else "?"
    flag = ("🎯 AGAINST market — edge possible" if vs == "AGAINST"
            else "chalk (market agrees, no edge)" if vs == "with" else "no market")
    print(f"  logged: {team} ({confidence}) @ market {od}  → {flag}")


def resolve():
    """Fill PENDING calls with WIN/LOSS by checking OpenDota outcomes."""
    if not os.path.exists(CALLS):
        print("  no calls"); return
    import yaml
    md = yaml.safe_load(open("markets.yaml"))
    rows = list(csv.DictReader(open(CALLS)))
    cache = {}
    changed = 0
    for r in rows:
        if r["result"] != "PENDING":
            continue
        mid = r["match_id"]
        if mid not in cache:
            try:
                cache[mid] = _get(f"https://api.opendota.com/api/matches/{mid}").get("radiant_win")
            except Exception:
                cache[mid] = None
        rw = cache[mid]
        if rw is None:
            continue
        mk = next((m for m in md.get("markets", []) if str(m.get("dota_match_id")) == mid), None)
        if not mk:
            continue
        # did our called team win? need radiant/dire mapping
        called = (r["called_team"] or "").lower()
        rad = (mk.get("steam_radiant_team") or mk.get("yes_team") or "").lower()
        called_is_radiant = called in rad or rad in called
        won = (rw and called_is_radiant) or ((not rw) and (not called_is_radiant))
        r["result"] = "WIN" if won else "LOSS"
        r["correct"] = "1" if won else "0"
        changed += 1
    if changed:
        with open(CALLS, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    print(f"  resolved {changed} calls")


def score():
    if not os.path.exists(CALLS):
        print("  no draft calls logged yet"); return
    rows = list(csv.DictReader(open(CALLS)))
    settled = [r for r in rows if r["result"] in ("WIN", "LOSS")]
    print(f"  draft calls: {len(rows)} | settled: {len(settled)}")
    if not settled:
        print("  (none settled yet — run --resolve after games finish)"); return
    c = sum(1 for r in settled if r["correct"] == "1")
    print(f"  raw accuracy: {c}/{len(settled)} = {c/len(settled)*100:.0f}%  (calling winners — NOT the edge metric)")
    # THE metric that matters: when I went AGAINST the market, did I win? + the $ edge.
    ag = [r for r in settled if r.get("vs_market") == "AGAINST"]
    ch = [r for r in settled if r.get("vs_market") == "with"]
    print(f"\n  === THE REAL TEST — beating the market ===")
    if ag:
        w = sum(1 for r in ag if r["correct"] == "1")
        # $ edge: betting an underdog at its odds that WINS pays (1/odds - 1)
        pnl = sum(((1.0/float(r["market_odds"]) - 1.0) if r["correct"] == "1" else -1.0)
                  for r in ag if r.get("market_odds"))
        print(f"  AGAINST market (called underdogs): {w}/{len(ag)} = {w/len(ag)*100:.0f}%  →  ${pnl:+.2f}/$1 staked")
        print(f"    ^ THIS is draft alpha. >0 = you beat the market. <=0 = no edge.")
    else:
        print("  AGAINST market: 0 calls yet — all your calls have been CHALK (agreeing w/ market).")
    if ch:
        w = sum(1 for r in ch if r["correct"] == "1")
        print(f"  chalk (agreed w/ market): {w}/{len(ch)} = {w/len(ch)*100:.0f}%  — even winning these = NO edge (market priced it)")
    print(f"\n  VERDICT: draft alpha exists ONLY if AGAINST-market $ is positive over 10+ such calls.")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        print(__doc__)
    elif a[0] == "--refresh":
        refresh_meta(force=True)
    elif a[0] == "--call" and len(a) >= 4:
        log_call(a[1], a[2], a[3])
    elif a[0] == "--resolve":
        resolve()
    elif a[0] == "--score":
        score()
    else:
        show(a[0])
