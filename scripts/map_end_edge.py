"""Test the MAP-END moneyline edge: when a map ends (game_over), the BO3 ML must
reprice to the new series state. Does the ML book LAG (stale quotes pickable) and
how well is it covered in our data?

For each match that reached game_over and has an ML market with book ticks:
  map_winner = sign of final net-worth lead
  measure the map-winner's ML token mid at T-30s (pre-end) vs T+30/60/120/300s.
  If it rises after the map ends → the ML lagged the certain new series state."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt
import yaml


def main():
    t0 = time.time()
    snaps = bt.load_snapshots()
    mk = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "markets.yaml"))["markets"]
    ml = {str(m["dota_match_id"]): m for m in mk
          if str(m.get("market_type")) == "MATCH_WINNER" and str(m.get("dota_match_id") or "").isdigit()}
    tokens = set()
    for m in ml.values():
        tokens.add(str(m["yes_token_id"])); tokens.add(str(m["no_token_id"]))
    book = bt.load_book_ticks(tokens)
    print(f"ML markets {len(ml)} | with book {sum(1 for m in ml.values() if str(m['yes_token_id']) in book)}  ({time.time()-t0:.0f}s)\n")

    # map-end = last snapshot with game_over True (or just the final decisive snapshot)
    horizons = [30, 60, 120, 300]
    res = {h: [] for h in horizons}
    have_end = covered = 0
    for mid, m in ml.items():
        if mid not in snaps or str(m["yes_token_id"]) not in book:
            continue
        rows = [r for r in snaps[mid] if r["gt"] is not None and r["rl"] is not None]
        if len(rows) < 5:
            continue
        # find map-end: first game_over row, else last row if it looks decisive
        end = None
        for r in rows:
            if r.get("go"):
                end = r; break
        if end is None:
            last = rows[-1]
            if (last["gt"] or 0) > 1200 and abs(int(last["rl"])) > 4000:
                end = last
        if end is None:
            continue
        have_end += 1
        radiant_won = 1 if int(end["rl"]) > 0 else 0
        srt = (m.get("steam_radiant_team") or "").strip(); yt = str(m["yes_token_id"]); nt = str(m["no_token_id"])
        yes_is_rad = bool(srt and m.get("yes_team") and srt.lower() == str(m["yes_team"]).lower())
        win_tok = (yt if yes_is_rad else nt) if radiant_won else (nt if yes_is_rad else yt)
        t_ns = end["ns"]
        p0 = bt.book_mid_at(book, win_tok, t_ns - 30 * 1_000_000_000)  # ~pre-end quote
        if p0 is None or p0 <= 0.02 or p0 >= 0.98:
            continue
        covered += 1
        for h in horizons:
            ph = bt.book_mid_at(book, win_tok, t_ns + h * 1_000_000_000)
            if ph is not None:
                res[h].append(ph - p0)
    print(f"map-ends detected: {have_end} | with usable pre-end ML quote: {covered}\n")
    print("=== map-WINNER's ML token move AFTER the map ends ===")
    print("  (positive = ML lagged the certain new series state → stale quote pickable)")
    for h in horizons:
        v = res[h]
        if not v:
            print(f"  +{h}s: no data"); continue
        v2 = sorted(v)
        print(f"  +{h:>3}s: n={len(v):>4}  avg {sum(v)/len(v)*100:+.2f}c  median {v2[len(v2)//2]*100:+.2f}c  %up {sum(1 for x in v if x>0)/len(v)*100:.0f}%")


if __name__ == "__main__":
    main()
