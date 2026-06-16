"""DEEP investigation of the decisive-swing (BO3 ML) strategy. The stress test showed the
edge is real when sellable (80% converge +0.10) but 39% of map-end exits are stale.

Question: does the book correct FAST (while the game is still live & liquid), so a fixed
short-horizon exit beats waiting for map-end? Investigates, per entry:
  A. convergence + liquidity at fixed seconds-after-entry horizons  (when does it correct? when is it liquid?)
  B. ROI by exit horizon (sell if fresh, else hold to series settlement)  -> best exit rule
  C. edge by entry-price bucket (is staleness/room bigger when entry is cheap?)
  D. swing->game-over time (how much live window do we even have to exit?)
"""
import bisect, math, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt
import yaml

random.seed(7)
DSWING_LEAD = 6000
FRESH_S = 180.0
HZ = [30, 60, 120, 300, 600, 1200]   # seconds after entry


def nearest(book, tok, ns):
    arr = book.get(tok)
    if not arr:
        return None, None
    times, mids = arr
    i = bisect.bisect_right(times, ns) - 1
    if i < 0:
        return None, None
    return mids[i], (ns - times[i]) / 1e9


def main():
    m2info = bt.load_mapping()
    snaps = bt.load_snapshots()
    mk = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "markets.yaml"))["markets"]
    mtype = {str(m["dota_match_id"]): str(m.get("market_type")) for m in mk if str(m.get("dota_match_id") or "").isdigit()}
    srt = {str(m["dota_match_id"]): m for m in mk if str(m.get("dota_match_id") or "").isdigit()}
    joinable = [mid for mid in snaps if mid in m2info]
    tokens = set()
    for mid in joinable:
        tokens.add(m2info[mid]["yes"]); tokens.add(m2info[mid]["no"])
    book = bt.load_book_ticks(tokens)
    joined = [mid for mid in joinable if m2info[mid]["yes"] in book and m2info[mid]["no"] in book]

    E = []
    for mid in joined:
        if mtype.get(mid, "") != "MATCH_WINNER":
            continue
        m = srt[mid]; info = m2info[mid]; yt, nt = info["yes"], info["no"]
        rows = [r for r in snaps[mid] if r["gt"] is not None and r["rl"] is not None]
        if len(rows) < 6:
            continue
        entry = next((r for r in rows if (r["gt"] or 0) > 600 and abs(int(r["rl"])) >= DSWING_LEAD), None)
        if entry is None:
            continue
        wr = 1 if int(entry["rl"]) > 0 else 0
        if (1 if int(rows[-1]["rl"]) > 0 else 0) != wr:
            continue
        sm = str(m.get("steam_radiant_team") or "")
        yir = bool(sm and m.get("yes_team") and sm.lower() == str(m["yes_team"]).lower())
        wtok = (yt if yir else nt) if wr else (nt if yir else yt)
        endr = next((r for r in rows if r.get("go")), rows[-1])
        m_in, _ = nearest(book, wtok, entry["ns"])
        if m_in is None:
            continue
        rec = dict(mid=mid, m_in=m_in, entry_ns=entry["ns"], entry_gt=entry["gt"],
                   go_ns=endr["ns"], sw=bt.get_asset_winner(book, wtok), hz={})
        for h in HZ:
            rec["hz"][h] = nearest(book, wtok, entry["ns"] + h * 1_000_000_000)  # (mid, age)
        rec["mout_go"] = nearest(book, wtok, endr["ns"] + 60 * 1_000_000_000)
        E.append(rec)

    N = len(E)
    print(f"=== DSWING DEEP INVESTIGATION  (n={N} entries, lead>={DSWING_LEAD}) ===")
    print(f"   avg entry price {sum(e['m_in'] for e in E)/N:.3f}\n")

    # D. swing -> game-over window
    wins = sorted((e["go_ns"] - e["entry_ns"]) / 1e9 for e in E)
    print("D. SWING -> GAME-OVER WINDOW  (how long the position is live before map ends)")
    print(f"   median {wins[len(wins)//2]/60:.1f} min   |  <2min: {100*sum(1 for w in wins if w<120)/N:.0f}%"
          f"   <5min: {100*sum(1 for w in wins if w<300)/N:.0f}%   >10min: {100*sum(1 for w in wins if w>600)/N:.0f}%\n")

    # A. convergence + liquidity at each horizon
    print("A. CONVERGENCE + LIQUIDITY by seconds-after-entry  (fresh = tick within 180s of target)")
    print(f"   {'horizon':>8} {'%fresh':>7} {'avg mid (fresh)':>16} {'avg move vs entry':>18}")
    for h in HZ:
        fr = [e for e in E if e["hz"][h][1] is not None and e["hz"][h][1] <= FRESH_S]
        if not fr:
            print(f"   {h:>6}s  {'0%':>7}"); continue
        am = sum(e["hz"][h][0] for e in fr) / len(fr)
        mv = sum(e["hz"][h][0] - e["m_in"] for e in fr) / len(fr)
        print(f"   {h:>6}s  {100*len(fr)/N:>6.0f}% {am:>16.3f} {mv:>+18.3f}")
    frg = [e for e in E if e["mout_go"][1] is not None and e["mout_go"][1] <= FRESH_S]
    print(f"   {'map-end':>7}  {100*len(frg)/N:>6.0f}% {sum(e['mout_go'][0] for e in frg)/len(frg):>16.3f}"
          f" {sum(e['mout_go'][0]-e['m_in'] for e in frg)/len(frg):>+18.3f}\n")

    # B. ROI by exit horizon (sell if fresh at horizon, else hold to series settlement)
    def roi_for_exit(get_target, slip):
        rets = []
        for e in E:
            e_in = min(e["m_in"] + slip, 0.99)
            mid, age = get_target(e)
            if mid is not None and age is not None and age <= FRESH_S:
                rets.append((max(mid - slip, 0.01) - e_in) / e_in)
            elif e["sw"] is not None:
                rets.append(((1.0 if e["sw"] == 1 else 0.0) - e_in) / e_in)
        return rets

    print("B. ROI BY EXIT RULE  (sell if liquid at exit, else hold to series settlement; slip 0.02)")
    print(f"   {'exit rule':>12} {'ROI':>7} {'P(>0)':>7} {'95% CI':>20} {'n':>4}")
    rules = [(f"+{h}s", (lambda h: (lambda e: e['hz'][h]))(h)) for h in HZ] + [("map-end", lambda e: e["mout_go"])]
    for label, fn in rules:
        r = roi_for_exit(fn, 0.02)
        if len(r) < 3:
            continue
        bs = sorted(sum(r[random.randrange(len(r))] for _ in range(len(r))) / len(r) for _ in range(8000))
        lo, hi = bs[200], bs[7800]; pos = sum(1 for b in bs if b > 0) / 8000
        print(f"   {label:>12} {100*sum(r)/len(r):>+6.1f}% {pos:>7.2f}  [{100*lo:>+5.1f}%,{100*hi:>+5.1f}%] {len(r):>4}")
    print()

    # C. edge by entry-price bucket
    print("C. EDGE BY ENTRY PRICE  (best-exit = +120s rule; is the edge bigger when entry is cheap?)")
    def best_ret(e, slip=0.02):
        e_in = min(e["m_in"] + slip, 0.99)
        mid, age = e["hz"][120]
        if mid is not None and age is not None and age <= FRESH_S:
            return (max(mid - slip, 0.01) - e_in) / e_in
        return ((1.0 if e["sw"] == 1 else 0.0) - e_in) / e_in if e["sw"] is not None else None
    for lo, hi in [(0.0, 0.6), (0.6, 0.75), (0.75, 0.85), (0.85, 1.01)]:
        idx = [e for e in E if lo <= e["m_in"] < hi]
        rr = [best_ret(e) for e in idx if best_ret(e) is not None]
        if not rr:
            print(f"   entry {lo:.2f}-{hi:.2f}: n=0"); continue
        print(f"   entry {lo:.2f}-{hi:.2f}: n={len(rr):>2}  ROI {100*sum(rr)/len(rr):>+6.1f}%")


if __name__ == "__main__":
    main()
