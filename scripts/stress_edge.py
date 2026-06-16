"""BRUTAL edge stress-test for the VALUE bot. Answers ONE question: is there a real
pricing edge, or is +16% ROI an artifact of an overconfident model + small n + optimistic fills?

For every trade the live config WOULD take (MAP_WINNER, lead>=3000, edge>=0.10, ask<=0.84,
flip-guard), it records (model_fair, market_mid, fill_ask, won) and runs:

  1. MODEL vs MARKET  -- whose probability better predicted the outcome (Brier + log-loss).
                         If the MARKET is >= the model, there is NO edge: we're betting our
                         model against an efficient price and the ROI is noise.
  2. CALIBRATION       -- when the model said ~70%, did ~70% actually win? (catches overconfidence)
  3. PESSIMISTIC FILLS -- does +EV survive ask + real slippage, not mid+0.005?
  4. ROI CONFIDENCE    -- bootstrap 95% CI on ROI. Is +X% even distinguishable from 0 at this n?

Winners from the asset's final book price; falls back to snapshot final lead."""
import math, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt
import winprob, yaml

random.seed(7)


def snap_winner_yes(side, rows):
    last = rows[-1]
    if (last["gt"] or 0) < 900 or abs(int(last["rl"])) < 2000:
        return None
    rw = 1 if int(last["rl"]) > 0 else 0
    return rw if side == "normal" else (1 - rw) if side == "reversed" else None


def brier(ps, ys):
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)


def logloss(ps, ys):
    e = 1e-6
    return -sum(y * math.log(min(max(p, e), 1 - e)) + (1 - y) * math.log(min(max(1 - p, e), 1 - e))
               for p, y in zip(ps, ys)) / len(ps)


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

    trades = []  # (fair, mid, ask, won)
    for mid in joined:
        info = m2info[mid]; yt, nt = info["yes"], info["no"]
        if mtype.get(mid, "") != "MAP_WINNER":
            continue
        rows = [r for r in snaps[mid] if r["gt"] is not None and r["rl"] is not None]
        if len(rows) < 6:
            continue
        yw = bt.get_asset_winner(book, yt)
        if yw is None:
            yw = snap_winner_yes(info["side"], rows)
        if yw is None:
            continue
        ey = en = False
        for cur in rows:
            if ey and en:
                break
            gt, rl = cur["gt"], int(cur["rl"])
            if gt < 600 or abs(rl) < 3000:
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
            if ask > 0.84:
                continue
            if abs(rl) > 5000 and ask < 0.35:
                continue
            fair = winprob.fair(abs(rl), gt, None)
            if fair - ask < 0.10:
                continue
            won = yw if tok == yt else (1 - yw)
            trades.append((fair, m0, ask, float(won)))
            if side == "YES":
                ey = True
            else:
                en = True

    n = len(trades)
    if n == 0:
        print("no trades"); return
    fairs = [t[0] for t in trades]; mids = [t[1] for t in trades]
    asks = [t[2] for t in trades]; ys = [t[3] for t in trades]
    wr = sum(ys) / n

    print(f"=== VALUE EDGE STRESS TEST  (n={n} trades) ===\n")

    # --- 1. MODEL vs MARKET (the crux) ---
    bm, bk = brier(fairs, ys), brier(mids, ys)
    lm, lk = logloss(fairs, ys), logloss(mids, ys)
    print("1. MODEL vs MARKET  (lower = better predictor of the actual outcome)")
    print(f"   Brier:   model {bm:.4f}   market {bk:.4f}   -> {'MODEL wins' if bm<bk else 'MARKET wins (NO EDGE)'}")
    print(f"   LogLoss: model {lm:.4f}   market {lk:.4f}   -> {'MODEL wins' if lm<lk else 'MARKET wins (NO EDGE)'}")
    print(f"   avg model_fair {sum(fairs)/n:.3f} | avg market_mid {sum(mids)/n:.3f} | realized winrate {wr:.3f}")
    print(f"   >> we paid avg {sum(asks)/n:.3f}, won {wr:.3f} of the time."
          f"  edge/contract = {wr-sum(asks)/n:+.3f}\n")

    # --- 2. CALIBRATION ---
    print("2. CALIBRATION  (does model_fair match reality? big gap = overconfident model)")
    for lo, hi in [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]:
        idx = [i for i in range(n) if lo <= fairs[i] < hi]
        if not idx:
            continue
        emp = sum(ys[i] for i in idx) / len(idx)
        af = sum(fairs[i] for i in idx) / len(idx)
        flag = "  <-- overconfident" if af - emp > 0.10 else ""
        print(f"   fair {lo:.1f}-{hi:.1f}: n={len(idx):>2}  model said {af:.2f}  reality {emp:.2f}{flag}")
    print()

    # --- 3. PESSIMISTIC FILLS ---
    print("3. PESSIMISTIC FILLS  (ROI as the fill price worsens; live fills are NOT mid+0.005)")
    for slip in [0.005, 0.01, 0.02, 0.03, 0.05]:
        rets = [(y - (m + slip)) / (m + slip) for m, y in zip(mids, ys)]
        roi = sum(rets) / n
        print(f"   slippage +{slip:.3f}: ROI {100*roi:+5.1f}%   ${5*sum(rets):+6.2f} on $5 stakes")
    print()

    # --- 4. ROI CONFIDENCE INTERVAL (bootstrap) ---
    print("4. ROI CONFIDENCE  (bootstrap 95% CI; if it straddles 0, edge is NOT proven at this n)")
    for slip, label in [(0.005, "optimistic"), (0.02, "realistic")]:
        rets = [(y - (m + slip)) / (m + slip) for m, y in zip(mids, ys)]
        boots = []
        for _ in range(20000):
            s = [rets[random.randrange(n)] for _ in range(n)]
            boots.append(sum(s) / n)
        boots.sort()
        lo, hi = boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots))]
        pos = sum(1 for b in boots if b > 0) / len(boots)
        print(f"   {label:10} (slip {slip:.3f}): ROI {100*sum(rets)/n:+5.1f}%  "
              f"95% CI [{100*lo:+5.1f}%, {100*hi:+5.1f}%]  P(ROI>0)={pos:.2f}")


if __name__ == "__main__":
    main()
