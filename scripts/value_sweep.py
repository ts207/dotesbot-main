"""VALUE BOT conviction sweep. The stress test showed the edge is concentrated in
high-conviction trades (0.8+ fair ~100% win) and diluted by coin-flips (0.6-0.7 fair
~50%). This finds the gate (min lead / min fair / min edge) that best lifts P(ROI>0)
off zero while keeping enough trades to matter.

ONE data load, then every config is evaluated in memory (the load is the slow part).
Hold-to-settle, winners from book final price / snapshot final lead, realistic slip 0.02."""
import random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt
import winprob, yaml

random.seed(7)
SLIP = 0.02   # realistic fill


def snap_winner_yes(side, rows):
    last = rows[-1]
    if (last["gt"] or 0) < 900 or abs(int(last["rl"])) < 2000:
        return None
    rw = 1 if int(last["rl"]) > 0 else 0
    return rw if side == "normal" else (1 - rw) if side == "reversed" else None


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

    # precompute per-match: rows, yes-winner, side
    M = []
    for mid in joined:
        if mtype.get(mid, "") != "MAP_WINNER":
            continue
        info = m2info[mid]
        rows = [r for r in snaps[mid] if r["gt"] is not None and r["rl"] is not None]
        if len(rows) < 6:
            continue
        yw = bt.get_asset_winner(book, info["yes"])
        if yw is None:
            yw = snap_winner_yes(info["side"], rows)
        if yw is None:
            continue
        M.append((mid, info, rows, yw))

    def select(min_lead, min_fair, min_edge, max_price=0.84):
        trades = []
        for mid, info, rows, yw in M:
            yt, nt = info["yes"], info["no"]
            ey = en = False
            for cur in rows:
                if ey and en:
                    break
                gt, rl = cur["gt"], int(cur["rl"])
                if gt < 600 or abs(rl) < min_lead:
                    continue
                d = "radiant" if rl > 0 else "dire"
                side = ("YES" if d == "radiant" else "NO") if info["side"] == "normal" else \
                       ("NO" if d == "radiant" else "YES") if info["side"] == "reversed" else None
                if side is None or (side == "YES" and ey) or (side == "NO" and en):
                    continue
                tok = yt if side == "YES" else nt
                m0 = bt.book_mid_at(book, tok, cur["ns"])
                if m0 is None:
                    continue
                ask = m0 + 0.005
                if ask > max_price:
                    continue
                if abs(rl) > 5000 and ask < 0.35:    # flip guard
                    continue
                fair = winprob.fair(abs(rl), gt, None)
                if fair < min_fair or fair - ask < min_edge:
                    continue
                won = yw if tok == yt else (1 - yw)
                trades.append((m0, won))     # entry mid, outcome
                if side == "YES":
                    ey = True
                else:
                    en = True
        return trades

    def metrics(trades):
        n = len(trades)
        if n == 0:
            return None
        rets = [((w - (m + SLIP)) / (m + SLIP)) for m, w in trades]
        wr = sum(w for _, w in trades) / n
        roi = sum(rets) / n
        bs = sorted(sum(rets[random.randrange(n)] for _ in range(n)) / n for _ in range(10000))
        lo, hi = bs[250], bs[9750]; pos = sum(1 for b in bs if b > 0) / 10000
        return n, wr, roi, lo, hi, pos

    print(f"=== VALUE BOT CONVICTION SWEEP  (realistic slip {SLIP}, hold-to-settle) ===")
    print(f"  matches: {len(M)}\n")
    print(f"  {'lead':>5} {'minfair':>7} {'minedge':>7} | {'n':>3} {'win%':>5} {'ROI':>6} {'P(>0)':>6} {'95% CI':>18}")
    print("  " + "-" * 70)
    grid = []
    for lead in (2000, 2500, 3000):
        for mf in (0.0, 0.65, 0.70, 0.75):
            for me in (0.10, 0.15):
                r = metrics(select(lead, mf, me))
                if not r:
                    continue
                n, wr, roi, lo, hi, pos = r
                grid.append((pos, roi, n, lead, mf, me, wr, lo, hi))
                star = " *" if (n >= 15 and pos >= 0.95) else ""
                print(f"  {lead:>5} {mf:>7.2f} {me:>7.2f} | {n:>3} {100*wr:>4.0f}% {100*roi:>+5.1f}% {pos:>6.2f}  [{100*lo:>+5.1f}%,{100*hi:>+5.1f}%]{star}")
    # best by P(>0) among configs with n>=15
    elig = [g for g in grid if g[2] >= 15]
    elig.sort(reverse=True)
    print("\n  BEST (n>=15, ranked by P(ROI>0) then ROI):")
    for pos, roi, n, lead, mf, me, wr, lo, hi in elig[:5]:
        print(f"    lead>={lead} fair>={mf:.2f} edge>={me:.2f}:  n={n} win={100*wr:.0f}% ROI={100*roi:+.1f}% P(>0)={pos:.2f}")


if __name__ == "__main__":
    main()
