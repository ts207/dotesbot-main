#!/usr/bin/env python3
"""Backtest the filtered value strategy (price floor + edge cap + time cap +
higher lead floor) vs the current config, on one data load. Reuses loaders."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt
import winprob

NOTIONAL = 5.0
CHS = 0.005


def load():
    m2info = bt.load_mapping()
    snaps = bt.load_snapshots()
    joinable = [mid for mid in snaps if mid in m2info]
    tokens = set()
    for mid in joinable:
        tokens.add(m2info[mid]["yes"]); tokens.add(m2info[mid]["no"])
    book = bt.load_book_ticks(tokens)
    joined = [mid for mid in joinable if m2info[mid]["yes"] in book and m2info[mid]["no"] in book]
    return m2info, snaps, book, joined


def _snapshot_winner(info, rows):
    """yes_token win (1/0) from the FINAL decisive net-worth lead via side-mapping
    (same mapping the engine uses). None if no decisive end. Covers ALL matches,
    not just those whose book price cleanly settled."""
    last = None
    for cur in rows:
        if cur["gt"] is not None and cur["rl"] is not None:
            last = cur
    if last is None or (last["gt"] or 0) < 900 or abs(int(last["rl"])) < 2000:
        return None
    radiant_won = 1 if int(last["rl"]) > 0 else 0
    sm = info["side"]
    if sm == "normal":
        return radiant_won
    if sm == "reversed":
        return 1 - radiant_won
    return None


def sim(m2info, snaps, book, joined, *, min_lead, min_time, max_time, min_edge, max_edge, min_price):
    trades = []
    for mid in joined:
        info = m2info[mid]; yt, nt = info["yes"], info["no"]
        yw = bt.get_asset_winner(book, yt)
        if yw is None:
            yw = _snapshot_winner(info, snaps[mid])   # fallback → covers all 130
        if yw is None:
            continue
        ey = en = False
        for cur in snaps[mid]:
            if ey and en:
                break
            gt, rl = cur["gt"], cur["rl"]
            if gt is None or gt < min_time or gt > max_time or rl is None:
                continue
            try:
                rl = int(rl)
            except ValueError:
                continue
            if abs(rl) < min_lead:
                continue
            d = "radiant" if rl > 0 else "dire"
            sm = info["side"]
            side = ("YES" if d == "radiant" else "NO") if sm == "normal" else \
                   ("NO" if d == "radiant" else "YES") if sm == "reversed" else None
            if side is None or (side == "YES" and ey) or (side == "NO" and en):
                continue
            tok = yt if side == "YES" else nt
            m0 = bt.book_mid_at(book, tok, cur["ns"])
            if m0 is None:
                continue
            ask = m0 + CHS
            if ask > 0.84 or ask < min_price:
                continue
            if abs(rl) > 5000 and ask < 0.35:
                continue
            edge = winprob.fair(abs(rl), gt, None) - ask
            if edge < min_edge or edge > max_edge:
                continue
            won = yw if tok == yt else (1 - yw)
            trades.append(((1.0 if won else 0.0) - ask) / ask * NOTIONAL)
            if side == "YES":
                ey = True
            else:
                en = True
    return trades


def stat(label, ts):
    if not ts:
        print(f"  {label:34} n=0"); return
    n = len(ts); w = sum(1 for p in ts if p > 0); tot = sum(ts)
    print(f"  {label:34} n={n:>4}  win%={100*w/n:>4.0f}  $/trade={tot/n:+.3f}  total=${tot:+7.2f}  ROI={100*tot/(NOTIONAL*n):+5.1f}%")


def main():
    t0 = time.time()
    data = load()
    print(f"loaded {len(data[3])} matches ({time.time()-t0:.0f}s)\n")
    INF = 10 ** 9
    print("=== CURRENT vs FILTERED ===")
    stat("CURRENT (lead3k, edge.10, p<.84)", sim(*data, min_lead=3000, min_time=600, max_time=INF, min_edge=0.10, max_edge=1.0, min_price=0.0))
    stat("+ price floor 0.50", sim(*data, min_lead=3000, min_time=600, max_time=INF, min_edge=0.10, max_edge=1.0, min_price=0.50))
    stat("+ edge cap 0.30", sim(*data, min_lead=3000, min_time=600, max_time=INF, min_edge=0.10, max_edge=0.30, min_price=0.0))
    stat("+ time cap 30min", sim(*data, min_lead=3000, min_time=600, max_time=1800, min_edge=0.10, max_edge=1.0, min_price=0.0))
    stat("ALL filters (3k lead)", sim(*data, min_lead=3000, min_time=600, max_time=1800, min_edge=0.10, max_edge=0.30, min_price=0.50))
    stat("ALL filters + lead 5k", sim(*data, min_lead=5000, min_time=600, max_time=1800, min_edge=0.10, max_edge=0.30, min_price=0.50))


if __name__ == "__main__":
    main()
