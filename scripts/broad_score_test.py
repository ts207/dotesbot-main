"""BROAD test (no order book needed -> big n) of the user's thesis applied everywhere, not just
reversal: kill-INDEPENDENT (structural/farm) net-worth is durable & predictive; kill-DRIVEN
(teamfight) net-worth is bursty & reverts. Uses snapshots + ground-truth outcomes only.

A. ENTRY: at a team's first 3k+ GOLD lead, bucket by how kill-heavy the lead is. Does a farm/
   structural lead (gold ahead, kills ~even) win MORE than a fight-driven lead (gold + big kills)?
B. SWING: after a 3k lead, first adverse gold swing -> gold-only vs co-swing(kills confirm).
   Does the leader hold up differently? (the coswing finding, now at big n)
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt

WIN_NS = 90 * 1_000_000_000
NW_SWING, KILL_SWING = 2000, 2
OUT = {str(k): bool(v) for k, v in json.load(open(Path(__file__).resolve().parent.parent / "logs" / "opendota_outcomes.json")).items()}


def back(rows, j, target_ns, key):
    for k in range(j, -1, -1):
        if rows[k]["ns"] <= target_ns:
            return rows[k][key]
    return None


def main():
    snaps = bt.load_snapshots()
    entry = []   # (kill_lead_at_3k, leader_won)
    swing = {"gold_only": [], "coswing": [], "no_swing": []}
    n_match = 0
    for mid, rws in snaps.items():
        rw = OUT.get(str(mid))
        if rw is None:
            continue
        rows = [r for r in rws if r["gt"] is not None and r["rl"] is not None]
        if len(rows) < 6:
            continue
        # first 3k+ gold lead
        i0 = None
        for i, cur in enumerate(rows):
            if (cur["gt"] or 0) >= 600 and abs(int(cur["rl"])) >= 3000:
                i0 = i; break
        if i0 is None:
            continue
        n_match += 1
        r0 = rows[i0]; sgn = 1 if int(r0["rl"]) > 0 else -1
        leader_won = 1 if ((rw and sgn > 0) or ((not rw) and sgn < 0)) else 0
        kill_lead0 = (r0["rs"] - r0["ds"]) * sgn
        entry.append((kill_lead0, leader_won))
        # first adverse gold swing after, classify
        kind = "no_swing"
        for j in range(i0 + 1, len(rows)):
            cur = rows[j]
            nw_now = int(cur["rl"]) * sgn
            nwp = back(rows, j, cur["ns"] - WIN_NS, "rl")
            if nwp is None:
                continue
            if (nwp * sgn) - nw_now < NW_SWING:
                continue
            rsp, dsp = back(rows, j, cur["ns"] - WIN_NS, "rs"), back(rows, j, cur["ns"] - WIN_NS, "ds")
            if rsp is None or dsp is None:
                continue
            k_now = (cur["rs"] - cur["ds"]) * sgn
            k_drop = ((rsp - dsp) * sgn) - k_now
            kind = "coswing" if k_drop >= KILL_SWING else "gold_only"
            break
        swing[kind].append(leader_won)

    print(f"=== BROAD SCORE TEST  (matches with 3k lead + ground truth: {n_match}) ===\n")
    print("A. ENTRY — win rate by how kill-heavy the first 3k GOLD lead is:")
    bins = [(-99, 0, "kills EVEN/behind (pure farm lead)"), (1, 3, "kills +1..3 (mild)"),
            (4, 7, "kills +4..7 (fight-ish)"), (8, 99, "kills +8 (fight-heavy)")]
    for lo, hi, lab in bins:
        g = [w for kl, w in entry if lo <= kl <= hi]
        if not g:
            print(f"   {lab:34} n=0"); continue
        print(f"   {lab:34} n={len(g):>3}  leader won {100*sum(g)/len(g):>4.0f}%")
    allw = sum(w for _, w in entry) / len(entry)
    print(f"   {'ALL 3k-gold leads':34} n={len(entry):>3}  leader won {100*allw:>4.0f}%\n")

    print("B. SWING — leader win rate after first adverse gold swing (gold-only vs teamfight co-swing):")
    for k in ("no_swing", "gold_only", "coswing"):
        g = swing[k]
        if not g:
            print(f"   {k:10} n=0"); continue
        print(f"   {k:10} n={len(g):>3}  leader won {100*sum(g)/len(g):>4.0f}%")
    print("\n  Thesis holds if: (A) farm leads win >= fight leads, and (B) gold_only swing << coswing (silent bleed = real danger).")


if __name__ == "__main__":
    main()
