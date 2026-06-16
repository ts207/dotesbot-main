"""Draft-vs-MARKET backtest — the real test of draft alpha.

Uses the clean dataset (has Polymarket book + outcomes), fetches each match's
draft from OpenDota, and asks: if I bet my BLIND draft call at the MARKET'S
earliest odds, do I make money? Beating the market price — not a coinflip — is
the only thing that proves a betting edge.

  --build N : assemble N matches (draft + earliest market odds + outcome), cache,
              display drafts ONLY (blind).
  --reveal "A,B,..." : score calls vs outcomes AND compute $ P&L betting each call
              at the market's odds. Disagreement (underdog) calls broken out —
              that's where alpha lives.
"""
from __future__ import annotations
import json, os, sys, time, csv, urllib.request

DATA = "logs/draftbook_data.json"
HEROES = "logs/hero_id_map.json"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    return json.load(urllib.request.urlopen(req, timeout=25))


def hero_map():
    if os.path.exists(HEROES):
        return {int(k): v for k, v in json.load(open(HEROES)).items()}
    m = {h["id"]: h["localized_name"] for h in _get("https://api.opendota.com/api/heroes")}
    json.dump(m, open(HEROES, "w"))
    return m


def _fnum(x):
    try:
        return float(x)
    except Exception:
        return None


def _earliest_odds(match_id):
    """Earliest captured YES ask from the clean snapshot = market's early price for yes_team."""
    p = f"data/clean/snapshots/{match_id}.csv"
    if not os.path.exists(p):
        return None
    for s in csv.DictReader(open(p)):
        ya = _fnum(s.get("yes_ask"))
        if ya and 0.05 < ya < 0.95:
            return round(ya, 3)
    return None


def build(n):
    hm = hero_map()
    idx = {r["match_id"]: r for r in csv.DictReader(open("data/clean/matches.csv"))}
    cand = [r for r in idx.values() if r["has_book"] == "1" and r["yes_won"] in ("0", "1")]
    games = []
    for m in cand:
        if len(games) >= n:
            break
        odds = _earliest_odds(m["match_id"])
        if odds is None:
            continue
        try:
            d = _get(f"https://api.opendota.com/api/matches/{m['match_id']}")
            time.sleep(1.0)
        except Exception:
            continue
        pl = d.get("players") or []
        rw = d.get("radiant_win")
        if rw is None or len(pl) != 10:
            continue
        rad = [hm.get(p.get("hero_id"), "?") for p in pl if p.get("isRadiant")]
        dire = [hm.get(p.get("hero_id"), "?") for p in pl if not p.get("isRadiant")]
        # map yes_team to radiant/dire via mapping
        mapping = (m.get("mapping") or "normal")
        # yes_team heroes: normal → yes=radiant ; reversed → yes=dire
        yes_heroes = rad if mapping == "normal" else dire
        no_heroes = dire if mapping == "normal" else rad
        games.append({
            "match_id": m["match_id"],
            "A_team": m["yes_team"], "A": yes_heroes, "A_odds": odds,   # A = YES side, market price = odds
            "B_team": m["no_team"], "B": no_heroes, "B_odds": round(1 - odds, 3),
            "A_won": m["yes_won"] == "1",
        })
        print(f"    built {len(games)}/{n}", flush=True)
    json.dump(games, open(DATA, "w"))
    print(f"\n=== BLIND DRAFTS + (hidden) market odds ({len(games)} games) ===")
    for i, g in enumerate(games, 1):
        print(f"\n  GAME {i}:")
        print(f"    A = {g['A_team']:<16} {', '.join(g['A'])}")
        print(f"    B = {g['B_team']:<16} {', '.join(g['B'])}")
    print(f"\n  → call A/B for each (drafts only), then --reveal \"A,B,...\"")


def reveal(calls_str):
    games = json.load(open(DATA))
    calls = [c.strip().upper() for c in calls_str.split(",") if c.strip()]
    n = min(len(calls), len(games))
    correct = pnl = 0.0
    dis = []      # disagreement (called the market underdog)
    chalk = []
    print(f"\n=== REVEAL — draft vs MARKET ({n} games) ===")
    for i in range(n):
        g = games[i]; call = calls[i]
        won_team = "A" if g["A_won"] else "B"
        hit = call == won_team
        correct += hit
        odds = g[f"{call}_odds"]            # market price for the team I called
        # bet $1 at market odds: win → (1/odds - 1), lose → -1
        p = (1.0/odds - 1.0) if hit else -1.0
        pnl += p
        tag = "DISAGREE(underdog)" if odds < 0.50 else "chalk(fav)"
        (dis if odds < 0.50 else chalk).append((hit, p))
        print(f"  G{i+1}: call {call} @ mkt {odds:.2f} [{tag:<18}] won={won_team}  {'✓' if hit else '✗'}  pnl={p:+.2f}")
    print(f"\n  ACCURACY: {correct:.0f}/{n} = {correct/n*100:.0f}%")
    print(f"  $ P&L betting every call at market odds: {pnl:+.2f}/{n} = {pnl/n:+.3f}/$1")
    print(f"\n  === THE ALPHA TEST — disagreement (underdog) calls ===")
    if dis:
        dw = sum(1 for h, p in dis if h); dp = sum(p for h, p in dis)
        print(f"  DISAGREE w/ market: {dw}/{len(dis)} = {dw/len(dis)*100:.0f}%  →  {dp:+.2f}/$1  ({dp/len(dis):+.3f} avg)")
        print(f"    ^ THIS is draft alpha. >0 = beat the market. <=0 = no betting edge.")
    else:
        print("  no disagreement calls (all chalk).")
    if chalk:
        cw = sum(1 for h, p in chalk if h); cp = sum(p for h, p in chalk)
        print(f"  chalk (called favorites): {cw}/{len(chalk)} = {cw/len(chalk)*100:.0f}%  →  {cp:+.2f}/$1 (fair-odds, ~0 edge expected)")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--build":
        build(int(a[1]) if len(a) > 1 else 20)
    elif a and a[0] == "--reveal" and len(a) > 1:
        reveal(a[1])
    else:
        print(__doc__)
