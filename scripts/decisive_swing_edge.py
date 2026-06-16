"""Test the decisive-swing edge on the BO3 ML book:
  ENTRY  = the moment the net-worth lead crosses a game-ending threshold (the swing
           that locks the game) — buy the now-near-certain side's ML at the (stale) quote.
  EXIT   = after the map ends and the ML has repriced (map-end + 60s).
  EDGE   = exit_price - entry_price  (the convergence you'd capture).
Also reports how often the decisive lead actually held (winner == decisive leader) and
data coverage (ML book present at both entry and exit)."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt
import yaml

DECISIVE = 8000   # lead crossing this = game-ending swing


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

    n_decisive = n_held = n_covered = 0
    edges = []; entry_px = []
    for mid, m in ml.items():
        if mid not in snaps or str(m["yes_token_id"]) not in book:
            continue
        rows = [r for r in snaps[mid] if r["gt"] is not None and r["rl"] is not None]
        if len(rows) < 6:
            continue
        # entry: first row (gt>600) where |lead| crosses DECISIVE
        entry = next((r for r in rows if (r["gt"] or 0) > 600 and abs(int(r["rl"])) >= DECISIVE), None)
        if entry is None:
            continue
        n_decisive += 1
        winner_rad = 1 if int(entry["rl"]) > 0 else 0
        final = rows[-1]
        final_rad = 1 if int(final["rl"]) > 0 else 0
        if final_rad != winner_rad:
            continue  # decisive lead was given back — not truly game-ending
        n_held += 1
        srt = (m.get("steam_radiant_team") or "").strip(); yt = str(m["yes_token_id"]); nt = str(m["no_token_id"])
        yes_is_rad = bool(srt and m.get("yes_team") and srt.lower() == str(m["yes_team"]).lower())
        win_tok = (yt if yes_is_rad else nt) if winner_rad else (nt if yes_is_rad else yt)
        # map-end: first game_over, else last row
        endr = next((r for r in rows if r.get("go")), final)
        e_in = bt.book_mid_at(book, win_tok, entry["ns"])
        e_out = bt.book_mid_at(book, win_tok, endr["ns"] + 60 * 1_000_000_000)
        if e_in is None or e_out is None or e_in <= 0.02 or e_in >= 0.98:
            continue
        n_covered += 1
        entry_px.append(e_in)
        edges.append(e_out - e_in)

    print(f"decisive swings (|lead|>={DECISIVE}): {n_decisive}")
    print(f"  ...that HELD (winner=decisive leader):  {n_held}  ({100*n_held/max(1,n_decisive):.0f}%)")
    print(f"  ...with ML book at BOTH entry+exit:     {n_covered}  <-- tradeable/measurable sample\n")
    if edges:
        s = sorted(edges)
        print("=== captured convergence (winner ML: map-end+60s  minus  decisive-swing entry) ===")
        print(f"  n={len(edges)}  avg {sum(edges)/len(edges)*100:+.2f}c  median {s[len(s)//2]*100:+.2f}c  %positive {100*sum(1 for x in edges if x>0)/len(edges):.0f}%")
        print(f"  entry price at decisive swing: avg {sum(entry_px)/len(entry_px):.2f}  (low = stale/underpriced winner)")
    else:
        print("no covered decisive swings — ML book wasn't logged around those moments")


if __name__ == "__main__":
    main()
