"""Test the user's core idea: a team that SLOWLY DRIFTS to a 3k lead isn't necessarily more
likely to win than one that WON A TEAMFIGHT to get there. So how the lead was *achieved*
(sudden swing vs gradual) should matter — and a teamfight should be the better entry.

For every value entry (back leader at lead>=3000, edge>=0.10, hold-to-settle), measure how
much of the lead was gained in the trailing ~90s (a teamfight timescale), bin by that, and
compare realized win rate + ROI. If higher recent-swing buckets win MORE, the user is right
and the entry should weight sudden swings. If flat, the level is what matters, not the swing."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt
import winprob, yaml

SLIP, STAKE = 0.02, 5.0
WINDOWS = [60, 90, 120]   # teamfight timescales (sec)


def snap_winner_yes(side, rows):
    last = rows[-1]
    if (last["gt"] or 0) < 900 or abs(int(last["rl"])) < 2000:
        return None
    rw = 1 if int(last["rl"]) > 0 else 0
    return rw if side == "normal" else (1 - rw) if side == "reversed" else None


def lead_before(rows, i, target_ns):
    for k in range(i, -1, -1):
        if rows[k]["ns"] <= target_ns:
            return int(rows[k]["rl"])
    return None


def main():
    m2info = bt.load_mapping()
    snaps = bt.load_snapshots()
    mk = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "markets.yaml"))["markets"]
    mtype = {str(m["dota_match_id"]): str(m.get("market_type")) for m in mk if str(m.get("dota_match_id") or "").isdigit()}
    joinable = [mid for mid in snaps if mid in m2info]
    tokens = set()
    for mid in joinable:
        tokens.add(m2info[mid]["yes"]); tokens.add(m2info[mid]["no"])
    book = bt.load_book_ticks(tokens)
    joined = [mid for mid in joinable if m2info[mid]["yes"] in book and m2info[mid]["no"] in book]

    # collect value entries with their recent-swing over each window
    E = []
    for mid in joined:
        if mtype.get(mid, "") != "MAP_WINNER":
            continue
        info = m2info[mid]; yt, nt = info["yes"], info["no"]
        rows = [r for r in snaps[mid] if r["gt"] is not None and r["rl"] is not None]
        if len(rows) < 6:
            continue
        yw = bt.get_asset_winner(book, yt)
        if yw is None:
            yw = snap_winner_yes(info["side"], rows)
        if yw is None:
            continue
        for i, cur in enumerate(rows):
            gt, rl = cur["gt"], int(cur["rl"])
            if gt < 600 or abs(rl) < 3000:
                continue
            d = "radiant" if rl > 0 else "dire"
            side = ("YES" if d == "radiant" else "NO") if info["side"] == "normal" else \
                   ("NO" if d == "radiant" else "YES") if info["side"] == "reversed" else None
            if side is None:
                continue
            tok = yt if side == "YES" else nt
            m0 = bt.book_mid_at(book, tok, cur["ns"])
            if m0 is None:
                continue
            ask = m0 + 0.005
            if ask > 0.84 or (abs(rl) > 5000 and ask < 0.35):
                continue
            if winprob.fair(abs(rl), gt, None) - ask < 0.10:
                continue
            sgn = 1 if rl > 0 else -1
            swings = {}
            for w in WINDOWS:
                past = lead_before(rows, i, cur["ns"] - w * 1_000_000_000)
                swings[w] = None if past is None else (rl - past) * sgn  # leader-perspective gain
            won = yw if tok == yt else (1 - yw)
            E.append(dict(ask=ask, won=won, swings=swings))
            break  # one entry per match (first qualifying)

    n = len(E)
    print(f"=== TEAMFIGHT vs DRIFT  (value entries: {n}) ===")
    print("  'recent swing' = net-worth lead gained by the leader in the trailing window before entry\n")

    def roi(group):
        if not group:
            return 0.0
        return sum(STAKE * (g["won"] - min(g["ask"] + SLIP, 0.99)) / min(g["ask"] + SLIP, 0.99) for g in group) / (STAKE * len(group))

    for w in WINDOWS:
        print(f"  -- trailing {w}s window --")
        bins = [(-99999, 500, "drift  (<0.5k)"), (500, 1500, "mild   (0.5-1.5k)"),
                (1500, 3000, "fight  (1.5-3k)"), (3000, 99999, "BIG fight (3k+)")]
        for lo, hi, lab in bins:
            g = [e for e in E if e["swings"][w] is not None and lo <= e["swings"][w] < hi]
            if not g:
                print(f"     {lab:18} n= 0"); continue
            wr = sum(x["won"] for x in g) / len(g)
            print(f"     {lab:18} n={len(g):>2}  win%={100*wr:>4.0f}  ROI={100*roi(g):>+5.0f}%  avg ask={sum(x['ask'] for x in g)/len(g):.2f}")
        print()
    print("  Read: if win% RISES with bigger recent swing -> teamfights predict better than drift (user right).")
    print("        if win% is FLAT across buckets -> the level is what matters, not how it was reached.")


if __name__ == "__main__":
    main()
