"""BRUTAL stress test for the DECISIVE-SWING (BO3 moneyline) strategy.

DSWING is NOT hold-to-settle: it buys the about-to-win team's ML right after a game-ending
swing (|lead|>=6000) and sells at map-end when the stale book corrects. Its backtest
(+14.8%/82%) SILENTLY DROPS matches with no exit book -- which is the strategy's whole
risk. This test refuses to drop them. For every qualifying entry it asks:

  1. EXIT LIQUIDITY  -- is there a FRESH book tick (<=180s) at map-end to actually sell into?
                        The backtest's survivorship bias lives here.
  2. CONVERGENCE     -- among sellable exits, did the price actually rise (the edge)?
  3. STUCK OUTCOMES  -- entries with NO fresh exit are FORCED to hold to series settlement.
                        Did the backed team win the series, or did it go to $0?
  4. HONEST ROI      -- full population (sold at exit + stuck held to settle) + bootstrap CI,
                        shown next to the survivorship view (sold-only) so the gap is visible.
"""
import bisect, math, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt
import yaml

random.seed(7)
DSWING_LEAD = 6000
FRESH_S = 180.0   # an exit tick older than this = not a real sellable quote


def nearest(book, tok, ns):
    """(mid, age_seconds) of the last tick at/before ns, or (None, None)."""
    arr = book.get(tok)
    if not arr:
        return None, None
    times, mids = arr
    i = bisect.bisect_right(times, ns) - 1
    if i < 0:
        return None, None
    return mids[i], (ns - times[i]) / 1e9


def boot_ci(rets):
    n = len(rets)
    bs = sorted(sum(rets[random.randrange(n)] for _ in range(n)) / n for _ in range(20000))
    return bs[int(0.025 * 20000)], bs[int(0.975 * 20000)], sum(1 for b in bs if b > 0) / 20000


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

    entries = []  # dict per qualifying entry
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
        # the leader at the swing must be the eventual game winner (decisive swing)
        if (1 if int(rows[-1]["rl"]) > 0 else 0) != wr:
            continue
        sm = str(m.get("steam_radiant_team") or "")
        yir = bool(sm and m.get("yes_team") and sm.lower() == str(m["yes_team"]).lower())
        wtok = (yt if yir else nt) if wr else (nt if yir else yt)   # winning team's ML token
        endr = next((r for r in rows if r.get("go")), rows[-1])
        m_in, age_in = nearest(book, wtok, entry["ns"])
        m_out, age_out = nearest(book, wtok, endr["ns"] + 60 * 1_000_000_000)
        if m_in is None:
            continue
        series_win = bt.get_asset_winner(book, wtok)   # 1 = backed team won the BO3
        entries.append(dict(mid=mid, m_in=m_in, m_out=m_out, age_out=age_out, sw=series_win))

    N = len(entries)
    print(f"=== DSWING (BO3 ML) STRESS TEST  (n={N} qualifying entries, lead>={DSWING_LEAD}) ===\n")
    if N == 0:
        print("no entries"); return

    # --- 1. EXIT LIQUIDITY ---
    fresh = [e for e in entries if e["age_out"] is not None and e["age_out"] <= FRESH_S]
    stale = [e for e in entries if e not in fresh]
    print("1. EXIT LIQUIDITY  (can we actually sell at map-end?)")
    print(f"   fresh exit tick (<= {FRESH_S:.0f}s):  {len(fresh)}/{N}  ({100*len(fresh)/N:.0f}%)")
    print(f"   stale / no exit book:           {len(stale)}/{N}  ({100*len(stale)/N:.0f}%)  <- backtest silently DROPS these")
    if stale:
        ages = [e["age_out"] for e in stale if e["age_out"] is not None]
        none_book = sum(1 for e in stale if e["age_out"] is None)
        med = sorted(ages)[len(ages)//2] if ages else float('nan')
        print(f"   of the stale: {none_book} had NO book at all; rest median staleness {med/60:.0f} min\n")

    # --- 2. CONVERGENCE (among sellable) ---
    if fresh:
        rose = sum(1 for e in fresh if e["m_out"] > e["m_in"])
        gross = sum(e["m_out"] - e["m_in"] for e in fresh) / len(fresh)
        print("2. CONVERGENCE  (among sellable exits -- the actual edge)")
        print(f"   price rose: {rose}/{len(fresh)} ({100*rose/len(fresh):.0f}%)   avg move {gross:+.3f}"
              f"   (entry {sum(e['m_in'] for e in fresh)/len(fresh):.3f} -> exit {sum(e['m_out'] for e in fresh)/len(fresh):.3f})\n")

    # --- 3. STUCK OUTCOMES (forced hold to series settlement) ---
    if stale:
        res = [e for e in stale if e["sw"] is not None]
        wonser = sum(1 for e in res if e["sw"] == 1)
        print("3. STUCK OUTCOMES  (no exit -> forced hold to SERIES settlement)")
        print(f"   resolvable: {len(res)}/{len(stale)}   backed team won series: {wonser}/{len(res)}"
              f" ({100*wonser/len(res) if res else 0:.0f}%)  -- the rest settled to $0\n")

    # --- 4. HONEST ROI: sell-if-fresh else hold-to-settle; survivorship view alongside ---
    def rets_at(slip, full):
        out = []
        for e in entries:
            e_in = min(e["m_in"] + slip, 0.99)
            if e["age_out"] is not None and e["age_out"] <= FRESH_S:
                e_out = max(e["m_out"] - slip, 0.01)
                out.append((e_out - e_in) / e_in)
            elif full:
                if e["sw"] is None:
                    continue   # truly unresolvable
                out.append(((1.0 if e["sw"] == 1 else 0.0) - e_in) / e_in)
            # if not full: skip stale (survivorship view)
        return out

    print("4. HONEST ROI  (5x stake; 'full' = stuck trades held to settlement, 'survivorship' = backtest view)")
    for slip in [0.005, 0.02, 0.05]:
        full = rets_at(slip, True); surv = rets_at(slip, False)
        rf = 100 * sum(full) / len(full) if full else float('nan')
        rs = 100 * sum(surv) / len(surv) if surv else float('nan')
        print(f"   slip +{slip:.3f}:  FULL ROI {rf:+6.1f}% (n={len(full)})   |   survivorship {rs:+6.1f}% (n={len(surv)})")
    print()
    print("   bootstrap 95% CI (realistic slip 0.02):")
    for full, label in [(True, "FULL (incl. stuck)"), (False, "survivorship")]:
        r = rets_at(0.02, full)
        if len(r) < 3:
            print(f"     {label:22}: n<3"); continue
        lo, hi, pos = boot_ci(r)
        print(f"     {label:22}: ROI {100*sum(r)/len(r):+6.1f}%  CI [{100*lo:+6.1f}%, {100*hi:+6.1f}%]  P(ROI>0)={pos:.2f}  n={len(r)}")


if __name__ == "__main__":
    main()
