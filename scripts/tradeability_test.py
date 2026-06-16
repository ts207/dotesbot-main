"""Continuation is real (lead extends ~2x after a kill-confirmed teamfight). DECISIVE question:
is it TRADEABLE, or has the price already moved by the time our GetTopLive snapshot shows the
kill swing ('price leads the feed')?

At each kill-confirmed teamfight, take the winning team's token price AT the snapshot, and see
where it goes over the next 60/120/180s. If the price keeps RISING after the snapshot, there's
a lag we can capture. If it's already jumped (flat/down after), it's priced and dead.
Compares teamfight vs gold-only. Needs order book + mapping (so n is book-limited)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt

LOOKBACK = 60 * 1_000_000_000
GOLD_MIN, KILL_CONF = 500, 2
HORIZONS = [60, 120, 180]


def back(rows, j, target_ns, key):
    for k in range(j, -1, -1):
        if rows[k]["ns"] <= target_ns:
            return rows[k][key]
    return None


def main():
    m2info = bt.load_mapping()
    snaps = bt.load_snapshots()
    tokens = set()
    for mid in snaps:
        if mid in m2info:
            tokens.add(m2info[mid]["yes"]); tokens.add(m2info[mid]["no"])
    book = bt.load_book_ticks(tokens)
    joined = [mid for mid in snaps if mid in m2info and m2info[mid]["yes"] in book and m2info[mid]["no"] in book]

    fight = {h: [] for h in HORIZONS}
    gold = {h: [] for h in HORIZONS}
    fpos = {h: 0 for h in HORIZONS}
    gpos = {h: 0 for h in HORIZONS}
    for mid in joined:
        info = m2info[mid]; yt, nt = info["yes"], info["no"]; side = info["side"]
        if side not in ("normal", "reversed"):
            continue
        rows = [r for r in snaps[mid] if r["gt"] is not None and r["rl"] is not None
                and r["rs"] is not None and r["ds"] is not None]
        if len(rows) < 8:
            continue
        last_ns = 0
        for j, cur in enumerate(rows):
            if (cur["gt"] or 0) < 600 or cur["ns"] - last_ns < LOOKBACK:
                continue
            rlp = back(rows, j, cur["ns"] - LOOKBACK, "rl")
            rsp, dsp = back(rows, j, cur["ns"] - LOOKBACK, "rs"), back(rows, j, cur["ns"] - LOOKBACK, "ds")
            if rlp is None or rsp is None:
                continue
            gold_sw = int(cur["rl"]) - int(rlp)
            if abs(gold_sw) < GOLD_MIN:
                continue
            X = 1 if gold_sw > 0 else -1                      # radiant won the swing?
            kill_sw = (cur["rs"] - cur["ds"]) - (rsp - dsp)
            kills_confirm = (kill_sw * X) >= KILL_CONF
            tokX = yt if ((X > 0) == (side == "normal")) else nt   # winning team's token
            p0 = bt.book_mid_at(book, tokX, cur["ns"])
            if p0 is None:
                continue
            last_ns = cur["ns"]
            for h in HORIZONS:
                pf = bt.book_mid_at(book, tokX, cur["ns"] + h * 1_000_000_000)
                if pf is None:
                    continue
                move = pf - p0                                # >0 = winner token still rose AFTER snapshot
                if kills_confirm:
                    fight[h].append(move); fpos[h] += 1 if move > 0.002 else 0
                else:
                    gold[h].append(move); gpos[h] += 1 if move > 0.002 else 0

    print(f"=== TRADEABILITY: does the winner's PRICE rise AFTER the teamfight snapshot? (matches: {len(joined)}) ===")
    print("  >0 = price lagged the swing (capturable);  ~0/neg = already priced (price-leads-feed, dead)\n")
    print(f"  {'horizon':>8} | {'TEAMFIGHT price move':>21} {'%up':>6} {'n':>5} | {'GOLD-ONLY price move':>21} {'%up':>6} {'n':>5}")
    for h in HORIZONS:
        f, g = fight[h], gold[h]
        if not f or not g:
            print(f"  {h:>6}s | insufficient"); continue
        print(f"  {h:>6}s | {sum(f)/len(f):>+18.3f}    {100*fpos[h]/len(f):>5.0f}% {len(f):>5} | "
              f"{sum(g)/len(g):>+18.3f}    {100*gpos[h]/len(g):>5.0f}% {len(g):>5}")
    print("\n  If TEAMFIGHT price move is solidly +0.02-0.05 and %up>>50 -> tradeable continuation edge.")
    print("  If ~0 -> the book already moved before our snapshot; real game edge but not tradeable.")


if __name__ == "__main__":
    main()
