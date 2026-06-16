"""Recalibrate the winprob model (temperature scaling) to fix the overconfidence the
stress test found (said 0.76 where reality was 0.70), then RE-RUN the full edge stress
test with the recalibrated model -- one data load for both.

Temperature T is fit on ONE qualifying game-state per joined match (~130 ~independent
points), minimizing log-loss of sigmoid(z/T) vs the leader's actual outcome. T is fit on
the broad population, NOT on the 30 entry-trades, so it can't overfit the test set.
Writes "temperature" into logs/winprob_model.json."""
import json, math, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt
import winprob, yaml

random.seed(7)
MODEL_PATH = Path(__file__).resolve().parent.parent / "logs" / "winprob_model.json"


def snap_winner_yes(side, rows):
    last = rows[-1]
    if (last["gt"] or 0) < 900 or abs(int(last["rl"])) < 2000:
        return None
    rw = 1 if int(last["rl"]) > 0 else 0
    return rw if side == "normal" else (1 - rw) if side == "reversed" else None


def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


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

    # ---- prep per-match resolved info ----
    def winner_yes(mid):
        info = m2info[mid]
        yw = bt.get_asset_winner(book, info["yes"])
        if yw is None:
            rows = [r for r in snaps[mid] if r["gt"] is not None and r["rl"] is not None]
            yw = snap_winner_yes(info["side"], rows) if rows else None
        return yw

    # ===== CALIBRATION SET: one qualifying state per MAP_WINNER match (leader z, outcome) =====
    cal_z, cal_y = [], []
    for mid in joined:
        if mtype.get(mid, "") != "MAP_WINNER":
            continue
        info = m2info[mid]; yt, nt = info["yes"], info["no"]
        rows = [r for r in snaps[mid] if r["gt"] is not None and r["rl"] is not None]
        if len(rows) < 6:
            continue
        yw = winner_yes(mid)
        if yw is None:
            continue
        first = next((r for r in rows if (r["gt"] or 0) >= 600 and abs(int(r["rl"])) >= 3000), None)
        if first is None:
            continue
        rl = int(first["rl"])
        d = "radiant" if rl > 0 else "dire"
        side = ("YES" if d == "radiant" else "NO") if info["side"] == "normal" else \
               ("NO" if d == "radiant" else "YES") if info["side"] == "reversed" else None
        if side is None:
            continue
        tok = yt if side == "YES" else nt
        p0 = winprob.fair(abs(rl), first["gt"], None)   # T=1 here (no temperature yet)
        cal_z.append(logit(p0))
        cal_y.append(float(yw if tok == yt else (1 - yw)))

    # fit temperature
    best_T, best_ll = 1.0, 1e9
    T = 0.80
    while T <= 3.001:
        ps = [1.0 / (1.0 + math.exp(-z / T)) for z in cal_z]
        ll = logloss(ps, cal_y)
        if ll < best_ll:
            best_ll, best_T = ll, T
        T += 0.01
    ll1 = logloss([1.0 / (1.0 + math.exp(-z)) for z in cal_z], cal_y)
    print(f"=== RECALIBRATION (temperature scaling) ===")
    print(f"  calibration points (1/match): {len(cal_z)}")
    print(f"  logloss T=1.00: {ll1:.4f}   ->   best T={best_T:.2f}: logloss {best_ll:.4f}")
    print(f"  avg model_fair T=1: {sum(1/(1+math.exp(-z)) for z in cal_z)/len(cal_z):.3f}"
          f"   recalibrated: {sum(1/(1+math.exp(-z/best_T)) for z in cal_z)/len(cal_z):.3f}"
          f"   reality: {sum(cal_y)/len(cal_y):.3f}")

    # write temperature into the model file
    md = json.load(open(MODEL_PATH))
    md["temperature"] = round(best_T, 3)
    json.dump(md, open(MODEL_PATH, "w"), indent=1)
    winprob._model = None  # force reload with new temperature
    print(f"  -> wrote temperature={best_T:.2f} to winprob_model.json\n")

    # ===== RE-RUN STRESS TEST with recalibrated fair =====
    trades = []  # (fair, mid, ask, won)
    for mid in joined:
        if mtype.get(mid, "") != "MAP_WINNER":
            continue
        info = m2info[mid]; yt, nt = info["yes"], info["no"]
        rows = [r for r in snaps[mid] if r["gt"] is not None and r["rl"] is not None]
        if len(rows) < 6:
            continue
        yw = winner_yes(mid)
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
            fair = winprob.fair(abs(rl), gt, None)   # now temperature-scaled
            if fair - ask < 0.10:
                continue
            won = yw if tok == yt else (1 - yw)
            trades.append((fair, m0, ask, float(won)))
            if side == "YES":
                ey = True
            else:
                en = True

    n = len(trades)
    print(f"=== VALUE EDGE STRESS TEST  (recalibrated, n={n} trades) ===\n")
    if n == 0:
        print("no trades survive the gate after recalibration"); return
    fairs = [t[0] for t in trades]; mids = [t[1] for t in trades]
    asks = [t[2] for t in trades]; ys = [t[3] for t in trades]
    wr = sum(ys) / n

    bm, bk = brier(fairs, ys), brier(mids, ys)
    lm, lk = logloss(fairs, ys), logloss(mids, ys)
    print("1. MODEL vs MARKET  (lower = better predictor)")
    print(f"   Brier:   model {bm:.4f}   market {bk:.4f}   -> {'MODEL wins' if bm<bk else 'MARKET wins (NO EDGE)'}")
    print(f"   LogLoss: model {lm:.4f}   market {lk:.4f}   -> {'MODEL wins' if lm<lk else 'MARKET wins (NO EDGE)'}")
    print(f"   avg model_fair {sum(fairs)/n:.3f} | avg market_mid {sum(mids)/n:.3f} | realized winrate {wr:.3f}")
    print(f"   >> paid avg {sum(asks)/n:.3f}, won {wr:.3f}  ->  edge/contract = {wr-sum(asks)/n:+.3f}\n")

    print("2. CALIBRATION (recalibrated)")
    for lo, hi in [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]:
        idx = [i for i in range(n) if lo <= fairs[i] < hi]
        if not idx:
            continue
        emp = sum(ys[i] for i in idx) / len(idx)
        af = sum(fairs[i] for i in idx) / len(idx)
        flag = "  <-- overconfident" if af - emp > 0.10 else ""
        print(f"   fair {lo:.1f}-{hi:.1f}: n={len(idx):>2}  model {af:.2f}  reality {emp:.2f}{flag}")
    print()

    print("3. PESSIMISTIC FILLS")
    for slip in [0.005, 0.02, 0.05]:
        rets = [(y - (m + slip)) / (m + slip) for m, y in zip(mids, ys)]
        print(f"   slip +{slip:.3f}: ROI {100*sum(rets)/n:+5.1f}%   ${5*sum(rets):+6.2f} on $5 stakes")
    print()

    print("4. ROI CONFIDENCE (bootstrap 95% CI)")
    for slip, label in [(0.005, "optimistic"), (0.02, "realistic")]:
        rets = [(y - (m + slip)) / (m + slip) for m, y in zip(mids, ys)]
        boots = sorted(sum(rets[random.randrange(n)] for _ in range(n)) / n for _ in range(20000))
        lo, hi = boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots))]
        pos = sum(1 for b in boots if b > 0) / len(boots)
        print(f"   {label:10} (slip {slip:.3f}): ROI {100*sum(rets)/n:+5.1f}%  "
              f"95% CI [{100*lo:+5.1f}%, {100*hi:+5.1f}%]  P(ROI>0)={pos:.2f}")


if __name__ == "__main__":
    main()
