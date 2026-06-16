#!/usr/bin/env python3
"""For every backtestable match, classify WHY it did or didn't generate a trade
under the current config (lead>=3000, time>=600, ask<=0.84, edge>=0.10)."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt
import winprob

CHS = 0.005


def main():
    t0 = time.time()
    m2info = bt.load_mapping()
    snaps = bt.load_snapshots()
    joinable = [mid for mid in snaps if mid in m2info]
    tokens = set()
    for mid in joinable:
        tokens.add(m2info[mid]["yes"]); tokens.add(m2info[mid]["no"])
    book = bt.load_book_ticks(tokens)
    joined = [mid for mid in joinable if m2info[mid]["yes"] in book and m2info[mid]["no"] in book]
    print(f"backtestable matches: {len(joined)}  ({time.time()-t0:.0f}s)\n")

    from collections import Counter
    verdict = Counter()
    for mid in joined:
        info = m2info[mid]; yt, nt = info["yes"], info["no"]
        reached_lead = had_book = had_price_ok = had_edge = traded = False
        any_snap_after_600 = False
        for cur in snaps[mid]:
            gt, rl = cur["gt"], cur["rl"]
            if gt is None or gt < 600 or rl is None:
                continue
            any_snap_after_600 = True
            try:
                rl = int(rl)
            except ValueError:
                continue
            if abs(rl) < 3000:
                continue
            reached_lead = True
            d = "radiant" if rl > 0 else "dire"
            sm = info["side"]
            side = ("YES" if d == "radiant" else "NO") if sm == "normal" else \
                   ("NO" if d == "radiant" else "YES") if sm == "reversed" else None
            if side is None:
                continue
            tok = yt if side == "YES" else nt
            m0 = bt.book_mid_at(book, tok, cur["ns"])
            if m0 is None:
                continue
            had_book = True
            ask = m0 + CHS
            if ask > 0.84:
                continue
            had_price_ok = True
            edge = winprob.fair(abs(rl), gt, None) - ask
            if edge >= 0.10:
                had_edge = traded = True
                break
        if traded:
            verdict["TRADED"] += 1
        elif not any_snap_after_600:
            verdict["no snapshots after 10min (short/early data)"] += 1
        elif not reached_lead:
            verdict["lead never reached 3000"] += 1
        elif not had_book:
            verdict["no book price at qualifying states"] += 1
        elif not had_price_ok:
            verdict["leader token always >0.84 (already priced in)"] += 1
        elif not had_edge:
            verdict["had lead+book but edge never >=0.10 (market efficient)"] += 1
        else:
            verdict["other"] += 1

    print("=== WHY EACH MATCH DID / DIDN'T TRADE ===")
    for k, n in verdict.most_common():
        print(f"  {k:52} {n}")


if __name__ == "__main__":
    main()
