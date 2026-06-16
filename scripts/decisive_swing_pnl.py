"""Full P&L backtest of the decisive-swing ML sniper, with realistic spread cost.
  ENTRY at the decisive swing: pay mid+half_spread (the ask).
  EXIT after map-end (+60s): sell at mid-half_spread (the bid).
  P&L per $NOTIONAL = (exit-entry)/entry * NOTIONAL.
Sweeps the decisive lead threshold; reports win%, $/trade, ROI, coverage."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt
import yaml

NOTIONAL = 5.0
HALF_SPREAD = 0.005   # ~1c round-trip; ML books are wider, so this is optimistic


def row_gt(row):
    return row.get("gt") if "gt" in row else row.get("game_time_sec")


def row_rl(row):
    return row.get("rl") if "rl" in row else row.get("radiant_lead")


def row_ns(row):
    return row.get("ns") if "ns" in row else row.get("received_at_ns")


def row_go(row):
    return row.get("go") if "go" in row else row.get("game_over")


def book_mid_at(book, token_id, ns):
    row = bt.book_at(book, token_id, int(ns))
    if not row:
        return None
    mid = row.get("mid")
    return None if mid is None else float(mid)


def run(snaps, ml, book, decisive):
    trades = []
    n_dec = n_held = 0
    for mid, m in ml.items():
        if mid not in snaps or str(m["yes_token_id"]) not in book:
            continue
        rows = [r for r in snaps[mid] if row_gt(r) is not None and row_rl(r) is not None]
        if len(rows) < 6:
            continue
        entry = next((r for r in rows if (row_gt(r) or 0) > 600 and abs(int(row_rl(r))) >= decisive), None)
        if entry is None:
            continue
        n_dec += 1
        wr = 1 if int(row_rl(entry)) > 0 else 0
        if (1 if int(row_rl(rows[-1])) > 0 else 0) != wr:
            continue
        n_held += 1
        srt = (m.get("steam_radiant_team") or "").strip(); yt = str(m["yes_token_id"]); nt = str(m["no_token_id"])
        yir = bool(srt and m.get("yes_team") and srt.lower() == str(m["yes_team"]).lower())
        win_tok = (yt if yir else nt) if wr else (nt if yir else yt)
        endr = next((r for r in rows if row_go(r)), rows[-1])
        m_in = book_mid_at(book, win_tok, row_ns(entry))
        m_out = book_mid_at(book, win_tok, row_ns(endr) + 60 * 1_000_000_000)
        if m_in is None or m_out is None:
            continue
        entry_px = min(m_in + HALF_SPREAD, 0.99)
        exit_px = max(m_out - HALF_SPREAD, 0.01)
        if entry_px <= 0.02 or entry_px >= 0.98:
            continue
        pnl = (exit_px - entry_px) / entry_px * NOTIONAL
        trades.append(pnl)
    return trades, n_dec, n_held


def main():
    t0 = time.time()
    mk = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "markets.yaml"))["markets"]
    ml = {str(m["dota_match_id"]): m for m in mk
          if str(m.get("market_type")) == "MATCH_WINNER" and str(m.get("dota_match_id") or "").isdigit()}
    snaps = bt.load_snapshots(set(ml))
    tokens = set()
    for m in ml.values():
        tokens.add(str(m["yes_token_id"])); tokens.add(str(m["no_token_id"]))
    book = bt.load_books(tokens)
    print(f"ML markets with book: {sum(1 for m in ml.values() if str(m['yes_token_id']) in book)}  ({time.time()-t0:.0f}s)\n")
    print("=== DECISIVE-SWING ML SNIPER P&L (entry=ask, exit=bid at map-end+60s) ===")
    for dec in (6000, 8000, 10000, 12000):
        tr, nd, nh = run(snaps, ml, book, dec)
        if not tr:
            print(f"  lead>={dec}: n=0 (decisive {nd}, held {nh})"); continue
        n = len(tr); w = sum(1 for x in tr if x > 0); tot = sum(tr)
        print(f"  lead>={dec:>5}: n={n:>3}  win%={100*w/n:>4.0f}  $/trade={tot/n:+.3f}  total=${tot:+6.2f}  ROI={100*tot/(NOTIONAL*n):+5.1f}%  (held {nh}/{nd})")


if __name__ == "__main__":
    main()
