#!/usr/bin/env python3
"""Test the BO3-moneyline-lag edge: when the game's net-worth lead swings, does the
MATCH_WINNER (series) book LAG (stale quotes you could pick off) or reprice instantly?

For each ML market: detect swing events from snapshots (lead jump in the leader's
direction), then measure the ML mid markout at +30s/+60s/+120s after the swing.
Positive markout in the swing direction = the book lagged = exploitable edge."""
import sys, time, bisect
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt


def main():
    t0 = time.time()
    m2info = bt.load_mapping()
    snaps = bt.load_snapshots()
    # restrict to MATCH_WINNER (BO3 ML) markets
    import yaml
    mk = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "markets.yaml"))["markets"]
    ml = {}  # match_id -> (yes_tok, no_tok, steam_radiant_team, yes_team)
    for m in mk:
        if str(m.get("market_type")) == "MATCH_WINNER" and str(m.get("dota_match_id") or "").isdigit():
            ml[str(m["dota_match_id"])] = m
    tokens = set()
    for mid, m in ml.items():
        tokens.add(str(m["yes_token_id"])); tokens.add(str(m["no_token_id"]))
    book = bt.load_book_ticks(tokens)
    print(f"ML markets: {len(ml)} | with book: "
          f"{sum(1 for m in ml.values() if str(m['yes_token_id']) in book)}  ({time.time()-t0:.0f}s)\n")

    def mid_at(tok, ns):
        return bt.book_mid_at(book, tok, ns)

    # collect swing events + markouts
    horizons = [30, 60, 120]
    results = {h: [] for h in horizons}   # signed ML markout in swing direction
    nsw = 0
    for mid, m in ml.items():
        yt, nt = str(m["yes_token_id"]), str(m["no_token_id"])
        if yt not in book:
            continue
        srt = (m.get("steam_radiant_team") or "").strip()
        yes_team = m.get("yes_team")
        # which ML token represents radiant?  (yes if steam_radiant==yes_team)
        yes_is_rad = (srt and yes_team and srt.lower() == str(yes_team).lower())
        rows = snaps[mid]
        if len(rows) < 6:
            continue
        for i in range(3, len(rows)):
            r0, r1 = rows[i - 3], rows[i]
            if r0["rl"] is None or r1["rl"] is None or r0["gt"] is None:
                continue
            d_lead = int(r1["rl"]) - int(r0["rl"])     # radiant-perspective lead change
            if abs(d_lead) < 2500:                       # require a real swing (~2.5k in ~window)
                continue
            # the team gaining = radiant if d_lead>0. Its ML token:
            gain_tok = (yt if yes_is_rad else nt) if d_lead > 0 else (nt if yes_is_rad else yt)
            t_ns = r1["ns"]
            p0 = mid_at(gain_tok, t_ns)
            if p0 is None or p0 <= 0.02 or p0 >= 0.98:
                continue
            nsw += 1
            for h in horizons:
                ph = mid_at(gain_tok, t_ns + h * 1_000_000_000)
                if ph is not None:
                    results[h].append(ph - p0)   # gaining team's ML token should RISE if book lagged
    print(f"swing events analysed: {nsw}\n")
    print("=== ML markout AFTER a game swing (gaining team's ML token) ===")
    print("  (positive = ML book lagged the swing → stale quote was pickable)")
    for h in horizons:
        v = results[h]
        if not v:
            print(f"  +{h}s: no data"); continue
        avg = sum(v) / len(v)
        pos = sum(1 for x in v if x > 0) / len(v)
        v.sort()
        med = v[len(v) // 2]
        print(f"  +{h:>3}s: n={len(v):>4}  avg move {avg*100:+.2f}c  median {med*100:+.2f}c  %moved-right {pos*100:.0f}%")


if __name__ == "__main__":
    main()
