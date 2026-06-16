#!/usr/bin/env python3
"""Comprehensive strategy analysis: replay the validated value strategy over the
historical snapshot+book data and break P&L down by lead, phase, entry price, and
edge — to find WHERE the edge actually lives. Reuses backtest_value_engine loaders."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt
import winprob

NOTIONAL = 5.0
COST_HALF_SPREAD = 0.005


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
    print(f"loaded: {len(joined)} matches with snapshots+book  ({time.time()-t0:.0f}s)\n")

    # validated config: lead>=3000, time>=600, edge>=0.10, price<=0.84
    trades = []
    for mid in joined:
        info = m2info[mid]; yes_tok, no_tok = info["yes"], info["no"]
        yes_won = bt.get_asset_winner(book, yes_tok)
        if yes_won is None:
            continue
        ey = en = False
        for cur in snaps[mid]:
            if ey and en:
                break
            gt, rl = cur["gt"], cur["rl"]
            if gt is None or gt < 600 or rl is None:
                continue
            try:
                rl = int(rl)
            except ValueError:
                continue
            if abs(rl) < 3000:
                continue
            direction = "radiant" if rl > 0 else "dire"
            sm = info["side"]
            side = ("YES" if direction == "radiant" else "NO") if sm == "normal" else \
                   ("NO" if direction == "radiant" else "YES") if sm == "reversed" else None
            if side is None or (side == "YES" and ey) or (side == "NO" and en):
                continue
            tok = yes_tok if side == "YES" else no_tok
            mid0 = bt.book_mid_at(book, tok, cur["ns"])
            if mid0 is None:
                continue
            ask = mid0 + COST_HALF_SPREAD
            if ask > 0.84:
                continue
            if abs(rl) > 5000 and ask < 0.35:   # orientation guard
                continue
            fair = winprob.fair(abs(rl), gt, None)
            edge = fair - ask
            if edge < 0.10:
                continue
            won = yes_won if tok == yes_tok else (1 - yes_won)
            payout = 1.0 if won else 0.0
            pnl = ((payout - ask) / ask) * NOTIONAL   # $NOTIONAL buys NOTIONAL/ask shares
            trades.append({"pnl": pnl, "won": won, "ask": ask, "edge": edge,
                           "lead": abs(rl), "gt": gt})
            if side == "YES":
                ey = True
            else:
                en = True

    def show(label, ts):
        if not ts:
            print(f"  {label:22} n=0"); return
        n = len(ts); w = sum(t["won"] for t in ts); tot = sum(t["pnl"] for t in ts)
        cap = NOTIONAL * n
        print(f"  {label:22} n={n:>4}  win%={100*w/n:>4.0f}  $/trade={tot/n:+.3f}  ROI={100*tot/cap:+5.1f}%")

    print(f"=== OVERALL ({len(trades)} trades) ===")
    show("ALL", trades)
    print("\n=== by NET-WORTH LEAD ===")
    for lo, hi in [(3000, 5000), (5000, 8000), (8000, 12000), (12000, 1e9)]:
        show(f"{lo//1000}-{hi//1000 if hi<1e9 else '+'}k", [t for t in trades if lo <= t["lead"] < hi])
    print("\n=== by GAME PHASE (minute) ===")
    for lo, hi in [(600, 1200), (1200, 1800), (1800, 2400), (2400, 9999)]:
        show(f"{lo//60}-{hi//60 if hi<9999 else '+'}min", [t for t in trades if lo <= t["gt"] < hi])
    print("\n=== by ENTRY PRICE (ask) ===")
    for lo, hi in [(0, 0.5), (0.5, 0.65), (0.65, 0.75), (0.75, 0.85)]:
        show(f"ask {lo}-{hi}", [t for t in trades if lo <= t["ask"] < hi])
    print("\n=== by EDGE bucket ===")
    for lo, hi in [(0.10, 0.15), (0.15, 0.20), (0.20, 0.30), (0.30, 1.0)]:
        show(f"edge {lo}-{hi}", [t for t in trades if lo <= t["edge"] < hi])


if __name__ == "__main__":
    main()
