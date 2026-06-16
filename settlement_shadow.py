"""Settlement-shadow log — the validation tool we actually need.

Every winner-set signal that FIRES live (whether or not it filled) is recorded
as a shadow trade, then stamped with its HOLD-TO-SETTLE outcome once the game
ends (via OpenDota). This gives a running LIVE tally of the real edge without
needing actual fills — the fastest way to confirm the projected 84% / +$14/match.

Unlike the old shadow_trades.csv (disabled — it measured useless 3-60s markouts),
this measures the ONLY horizon that matters for this strategy: settlement.

Run:
    python3 settlement_shadow.py          # one pass: reconcile + print tally
    python3 settlement_shadow.py --loop   # refresh every 5 min

Reads:  logs/latency.csv  (fired signals)  + markets.yaml (mapping)
Writes: logs/settlement_shadow.csv (the shadow ledger)
        logs/shadow_outcomes_cache.json (OpenDota cache)
"""
from __future__ import annotations
import csv, json, os, sys, time, urllib.request
from collections import defaultdict

WINNER = {"POLL_FIRST_SWING_SETTLE", "POLL_PHASE_NORMALIZED_LEAD",
          "POLL_VALUE_DISAGREEMENT", "POLL_RAPID_STOMP", "POLL_DECISIVE_STOMP"}
LAT = "logs/latency.csv"
LEDGER = "logs/settlement_shadow.csv"
CACHE = "logs/shadow_outcomes_cache.json"
BASE = 5.0  # 2026-06-01 — match MAX_TRADE_USD for the $100 live run (was 50 paper)


def fnum(x):
    try:
        return float(x)
    except Exception:
        return None


def conf_size(a):
    return 1.6 * (0.5 + (a - 0.45) / 0.40)


def load_mapping_by_token():
    """token_id -> (mapping, is_yes_token, team) from markets.yaml."""
    import yaml
    md = yaml.safe_load(open("markets.yaml"))
    out = {}
    for m in md.get("markets", []):
        mp = (m.get("steam_side_mapping") or "normal").strip()
        out[str(m.get("yes_token_id"))] = (mp, True, m.get("yes_team"), str(m.get("dota_match_id")))
        out[str(m.get("no_token_id"))] = (mp, False, m.get("no_team"), str(m.get("dota_match_id")))
    return out


def fetch_radiant_win(match_id, cache):
    if match_id in cache:
        return cache[match_id]
    try:
        req = urllib.request.Request(f"https://api.opendota.com/api/matches/{match_id}",
                                     headers={"User-Agent": "curl/8"})
        d = json.load(urllib.request.urlopen(req, timeout=20))
        rw = d.get("radiant_win")
        cache[match_id] = rw  # None if not parsed yet
        return rw
    except Exception:
        return None


def extract_signals():
    """First qualifying winner-set fire per (match, event, side) from latency.csv."""
    if not os.path.exists(LAT):
        return {}
    seen = {}
    with open(LAT) as f:
        r = csv.reader(f)
        next(r, None)  # header
        for row in r:
            if len(row) < 26:
                continue
            mid, et, gt = row[4], row[6], row[9]
            tok, side, ask = row[19], row[20], fnum(row[25])
            if et not in WINNER or side not in ("YES", "NO"):
                continue
            if ask is None or ask < 0.45 or ask > 0.85:
                continue
            key = (mid, et, side)
            if key in seen:
                continue
            seen[key] = {"ts": row[0], "match_id": mid, "event_type": et,
                         "side": side, "token_id": tok, "ask": ask, "game_time": gt}
    return seen


def reconcile():
    tok_map = load_mapping_by_token()
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    sigs = extract_signals()

    rows = []
    for key, s in sigs.items():
        mp_info = tok_map.get(s["token_id"])
        rw = fetch_radiant_win(s["match_id"], cache)
        won = pnl = None
        status = "PENDING"
        if mp_info and rw is not None:
            mapping, is_yes, team, _ = mp_info
            # our token's team is radiant if (yes & normal) or (no & reversed)
            our_is_radiant = (is_yes and mapping == "normal") or ((not is_yes) and mapping == "reversed")
            won = (rw and our_is_radiant) or ((not rw) and (not our_is_radiant))
            pnl = (1.0 - s["ask"]) if won else -s["ask"]
            status = "WIN" if won else "LOSS"
        elif mp_info and rw is None:
            status = "PENDING"  # game not settled / not parsed yet
        else:
            status = "NO_MAPPING"
        rows.append({**s, "status": status,
                     "pnl_per_1": ("%.3f" % pnl) if pnl is not None else "",
                     "sized_pnl": ("%.2f" % (pnl * conf_size(s["ask"]) * BASE)) if pnl is not None else ""})

    json.dump(cache, open(CACHE, "w"))
    cols = ["ts", "match_id", "event_type", "side", "token_id", "ask", "game_time",
            "status", "pnl_per_1", "sized_pnl"]
    with open(LEDGER, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})
    return rows


def live_actual_tally():
    """REAL filled live orders → their hold-to-settle outcome. This is the true
    live-vs-backtest verdict (the shadow tally above is hypothetical-per-signal).
    Uses the captured avg_fill_price (now logged) as the real entry; resolves the
    outcome the same way; flags positions that were exited early (live_exits)."""
    LIVE_ATT = "logs/live_attempts.csv"
    if not os.path.exists(LIVE_ATT):
        return
    tok_map = load_mapping_by_token()
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    # exited matches (sold before settlement) — note them. Lifecycle reconciliation
    # rows and failed exit attempts are not exits; they should not suppress
    # hold-to-settle P&L.
    exited = set()
    if os.path.exists("logs/live_exits.csv"):
        try:
            for r in csv.DictReader(open("logs/live_exits.csv")):
                status = (r.get("order_status") or "").lower()
                reason = (r.get("reason") or "").lower()
                filled = fnum(r.get("shares_filled")) or 0.0
                if reason.startswith("startup_") or status in {"lifecycle", "exception", ""}:
                    continue
                if filled > 0 or status in {"filled", "matched"}:
                    exited.add(str(r.get("match_id") or ""))
        except Exception:
            pass
    # dedupe filled buys by (match, side, token); sum size, weight entry price
    pos = {}
    for r in csv.DictReader(open(LIVE_ATT)):
        if r.get("order_status") != "filled":
            continue
        fs = fnum(r.get("filled_size_usd"))
        if not fs or fs <= 0:
            continue
        key = (r.get("match_id"), r.get("token_id"), r.get("side"))
        px = fnum(r.get("avg_fill_price"))
        d = pos.setdefault(key, {"usd": 0.0, "pxsum": 0.0, "n": 0, "event": r.get("event_type")})
        d["usd"] += fs
        if px:
            d["pxsum"] += px * fs; d["n"] += fs
    print("\n=== LIVE ACTUAL FILLS (real money, real entry prices) ===")
    if not pos:
        print("  no real fills yet")
        return
    settled = []
    for (mid, tok, side), d in pos.items():
        entry = (d["pxsum"] / d["n"]) if d["n"] else None
        rw = fetch_radiant_win(mid, cache)
        mp = tok_map.get(tok)
        line = f"  {mid} {side} ${d['usd']:.0f} @ {entry:.3f}" if entry else f"  {mid} {side} ${d['usd']:.0f}"
        if mid in exited:
            line += "  [EXITED early — pre-fix, not a clean hold-to-settle result]"
        elif mp and rw is not None:
            mapping, is_yes, team, _ = mp
            our_is_radiant = (is_yes and mapping == "normal") or ((not is_yes) and mapping == "reversed")
            won = (rw and our_is_radiant) or ((not rw) and (not our_is_radiant))
            pnl_d = d["usd"] * ((1.0/entry - 1.0) if won else -1.0) if entry else 0.0
            line += f"  -> {'WIN' if won else 'LOSS'}  P&L ${pnl_d:+.2f}"
            settled.append((won, pnl_d))
        else:
            line += "  -> PENDING settlement"
        print(line)
    json.dump(cache, open(CACHE, "w"))
    if settled:
        W = sum(1 for w, p in settled if w); tot = sum(p for w, p in settled)
        print(f"  --- REAL hold-to-settle record: {W}/{len(settled)} = {W/len(settled)*100:.0f}%  net ${tot:+.2f}  (backtest ref: 84%, +EV) ---")


def tally(rows):
    settled = [r for r in rows if r["status"] in ("WIN", "LOSS")]
    pending = [r for r in rows if r["status"] == "PENDING"]
    print(f"=== SETTLEMENT-SHADOW LIVE TALLY ===")
    print(f"  shadow signals recorded: {len(rows)}   settled: {len(settled)}   pending: {len(pending)}")
    if settled:
        W = sum(1 for r in settled if r["status"] == "WIN")
        tot1 = sum(float(r["pnl_per_1"]) for r in settled)
        totd = sum(float(r["sized_pnl"]) for r in settled)
        print(f"  LIVE win rate: {W}/{len(settled)} = {W/len(settled)*100:.1f}%")
        print(f"  LIVE edge: {tot1/len(settled):+.3f}/$1   total ${totd:+.0f} (conf-sized $50 base)")
        be = defaultdict(lambda: [0, 0])
        for r in settled:
            be[r["event_type"]][0] += int(r["status"] == "WIN"); be[r["event_type"]][1] += 1
        print("  by event:")
        for e, (w, t) in sorted(be.items()):
            print(f"    {e.replace('POLL_',''):<22} {w}/{t}")
    else:
        print("  (no settled shadow trades yet — waiting on live games to fire + settle)")
    if pending:
        print(f"  pending (awaiting settlement): " + ", ".join(sorted({r['match_id'] for r in pending}))[:200])


def main():
    loop = "--loop" in sys.argv
    while True:
        rows = reconcile()
        tally(rows)
        live_actual_tally()
        try:
            with open("logs/shadow_heartbeat", "w") as _hb:
                _hb.write(str(time.time()))
        except Exception:
            pass
        if not loop:
            break
        time.sleep(300)


if __name__ == "__main__":
    main()
