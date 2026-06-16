"""2D characterization of GOLD swings x KILL swings in the snapshots. For every moment a team
leads 3k+ gold, measure the trailing-90s swing in net-worth AND in kills (toward the trailing
team = adverse to the leader), and report the LEADER's eventual win rate across the grid.

Answers: holding gold-swing fixed, does the kill-swing change the outcome? (i.e. is a gold drop
WITH kills different from a gold drop WITHOUT kills?) Ground-truth outcomes, no order book.
Pooled over all qualifying snapshots (autocorrelated within a match — descriptive, not a t-test)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt

WIN_NS = 90 * 1_000_000_000
OUT = {str(k): bool(v) for k, v in json.load(open(Path(__file__).resolve().parent.parent / "logs" / "opendota_outcomes.json")).items()}


def back(rows, j, target_ns, key):
    for k in range(j, -1, -1):
        if rows[k]["ns"] <= target_ns:
            return rows[k][key]
    return None


def gbin(g):   # gold swing toward trailing team (adverse to leader)
    if g < 0:   return 0   # leader EXTENDING
    if g < 1000: return 1
    if g < 2500: return 2
    return 3
GL = ["A extending(<0)", "flat(0-1k)", "bleed(1-2.5k)", "BIG bleed(2.5k+)"]


def kbin(k):   # kill swing toward trailing team
    if k <= 0:  return 0   # leader gaining/even kills
    if k == 1:  return 1
    if k <= 3:  return 2
    return 3
KL = ["kills A+/even", "kills B+1", "kills B+2-3", "kills B+4"]


def main():
    snaps = bt.load_snapshots()
    grid = {(gi, ki): [] for gi in range(4) for ki in range(4)}
    n_match = 0
    for mid, rws in snaps.items():
        rw = OUT.get(str(mid))
        if rw is None:
            continue
        rows = [r for r in rws if r["gt"] is not None and r["rl"] is not None]
        if len(rows) < 8:
            continue
        used = False
        last_ns = 0
        for j, cur in enumerate(rows):
            if (cur["gt"] or 0) < 600 or abs(int(cur["rl"])) < 3000:
                continue
            if cur["ns"] - last_ns < WIN_NS:   # one sample per 90s to limit overlap
                continue
            sgn = 1 if int(cur["rl"]) > 0 else -1
            nwp = back(rows, j, cur["ns"] - WIN_NS, "rl")
            rsp, dsp = back(rows, j, cur["ns"] - WIN_NS, "rs"), back(rows, j, cur["ns"] - WIN_NS, "ds")
            if nwp is None or rsp is None or dsp is None:
                continue
            gold_sw = (nwp * sgn) - int(cur["rl"]) * sgn        # >0 = adverse (toward B)
            kill_sw = ((rsp - dsp) * sgn) - (cur["rs"] - cur["ds"]) * sgn
            leader_won = 1 if ((rw and sgn > 0) or ((not rw) and sgn < 0)) else 0
            grid[(gbin(gold_sw), kbin(kill_sw))].append(leader_won)
            last_ns = cur["ns"]; used = True
        n_match += 1 if used else 0

    print(f"=== GOLD-SWING x KILL-SWING -> leader win%  (matches: {n_match}, 90s windows) ===")
    print("   rows = gold swing toward trailing team;  cols = kill swing toward trailing team\n")
    print(f"   {'':18}" + "".join(f"{c:>16}" for c in KL))
    for gi in range(4):
        cells = []
        for ki in range(4):
            g = grid[(gi, ki)]
            cells.append(f"{100*sum(g)/len(g):>3.0f}% (n={len(g)})" if g else "   -    ")
        print(f"   {GL[gi]:18}" + "".join(f"{c:>16}" for c in cells))
    print("\n  Read each ROW left->right: at the SAME gold swing, does adding kills change leader win%?")
    print("  Thesis: in the 'BIG bleed' row, 'kills A+/even' (silent) should be LOWER than 'kills B+4' (teamfight).")


if __name__ == "__main__":
    main()
