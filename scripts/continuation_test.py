"""User's thesis, stated precisely: a TEAMFIGHT = kills swing toward a team AND net-worth swings
the SAME direction (in one snapshot interval). It does NOT matter who's leading. After such a
teamfight, that team should EXTEND its net-worth in the next minute (continuation/momentum).

Test: for every ~60s window, find which way gold swung. Split into:
  TEAMFIGHT  = kills confirm it (kill-lead moved >=2 the SAME way as gold)
  GOLD-ONLY  = gold moved but kills didn't (quiet farm/objective shift)
Then measure how much MORE net-worth moves the SAME direction over the next 60/120/180s.
If TEAMFIGHT continues >> GOLD-ONLY, kills confirm real momentum (user right). Snapshots only,
no leading requirement, no outcome needed."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt

LOOKBACK = 60 * 1_000_000_000
GOLD_MIN = 500       # gold actually moved (something happened)
KILL_CONF = 2        # kills moved >= this the same way = teamfight
HORIZONS = [60, 120, 180]


def fwd(rows, j, target_ns, key):
    for k in range(j, len(rows)):
        if rows[k]["ns"] >= target_ns:
            return rows[k][key]
    return None


def back(rows, j, target_ns, key):
    for k in range(j, -1, -1):
        if rows[k]["ns"] <= target_ns:
            return rows[k][key]
    return None


def main():
    snaps = bt.load_snapshots()
    fight = {h: [] for h in HORIZONS}
    goldonly = {h: [] for h in HORIZONS}
    fpos = {h: 0 for h in HORIZONS}
    gpos = {h: 0 for h in HORIZONS}
    nmatch = 0; last = {}
    for mid, rws in snaps.items():
        rows = [r for r in rws if r["gt"] is not None and r["rl"] is not None
                and r["rs"] is not None and r["ds"] is not None]
        if len(rows) < 8:
            continue
        nmatch += 1
        last_ns = 0
        for j, cur in enumerate(rows):
            if (cur["gt"] or 0) < 600:
                continue
            if cur["ns"] - last_ns < LOOKBACK:   # ~1 sample per window
                continue
            rlp = back(rows, j, cur["ns"] - LOOKBACK, "rl")
            rsp, dsp = back(rows, j, cur["ns"] - LOOKBACK, "rs"), back(rows, j, cur["ns"] - LOOKBACK, "ds")
            if rlp is None or rsp is None:
                continue
            gold_sw = int(cur["rl"]) - int(rlp)            # radiant perspective; sign = who gained gold
            if abs(gold_sw) < GOLD_MIN:
                continue
            X = 1 if gold_sw > 0 else -1                    # team that gained gold (radiant=+1)
            kill_sw = ((cur["rs"] - cur["ds"]) - (rsp - dsp))
            kills_confirm = (kill_sw * X) >= KILL_CONF      # kills moved the SAME way as gold by >=2
            last_ns = cur["ns"]
            for h in HORIZONS:
                fut = fwd(rows, j, cur["ns"] + h * 1_000_000_000, "rl")
                if fut is None:
                    continue
                cont = (int(fut) - int(cur["rl"])) * X      # >0 = gold kept moving X's way (extended)
                if kills_confirm:
                    fight[h].append(cont); fpos[h] += 1 if cont > 0 else 0
                else:
                    goldonly[h].append(cont); gpos[h] += 1 if cont > 0 else 0

    print(f"=== CONTINUATION after a swing — does the gold keep moving the same way? (matches: {nmatch}) ===")
    print(f"  window {LOOKBACK//10**9}s, gold move >= {GOLD_MIN}; TEAMFIGHT = kills confirm (+{KILL_CONF} same dir)\n")
    print(f"  {'horizon':>8} | {'TEAMFIGHT next-ext':>19} {'%cont':>7} {'n':>5} | {'GOLD-ONLY next-ext':>19} {'%cont':>7} {'n':>5}")
    for h in HORIZONS:
        f, g = fight[h], goldonly[h]
        if not f or not g:
            continue
        print(f"  {h:>6}s | {sum(f)/len(f):>+15.0f} gold {100*fpos[h]/len(f):>6.0f}% {len(f):>5} | "
              f"{sum(g)/len(g):>+15.0f} gold {100*gpos[h]/len(g):>6.0f}% {len(g):>5}")
    print("\n  Thesis holds if TEAMFIGHT continuation >> GOLD-ONLY (kills confirm the swing keeps going).")


if __name__ == "__main__":
    main()
