#!/usr/bin/env python3
"""First-principles study: how does game-state predict forward book moves?

This drops the event-detector framing entirely. Instead, for every top_live
snapshot in data_v2, we compute:

  Features (at snapshot time t):
    - Game state: game_time, radiant_lead, kill_diff, tower_state
    - 1-snap delta: Δlead, Δkill_diff, Δtower since previous snapshot
    - 3-snap delta: cumulative change over the last ~45s of game time
    - Book state: current mid, spread, ask_size, bid_size, book_imbalance
    - Pre-game mid: book mid at match start (narrative anchor)

  Labels (signed by direction of Δlead at t):
    - d_mid_5s, d_mid_10s, d_mid_30s, d_mid_60s

Then we ask:
  A. Which single features predict d_mid_30s best?
  B. What's the optimal threshold for the top predictor?
  C. Is there a continuous trading rule that beats the discrete event detector?
  D. Does the relationship change between the two tournaments?
"""
from __future__ import annotations

import bisect
import math
import sys
from collections import defaultdict
from pathlib import Path

import yaml
import pyarrow as pa
import pyarrow.dataset as pds
import pyarrow.compute as pc

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------- loaders ----------
def load_mapping():
    with open(REPO_ROOT / "markets.yaml") as f:
        mk = yaml.safe_load(f)
    return {m["dota_match_id"]: {"yes": m["yes_token_id"], "side": m.get("steam_side_mapping","normal")}
            for m in mk["markets"]
            if m.get("dota_match_id") and m["dota_match_id"].isdigit() and m["dota_match_id"]!="123"}


def load_snapshots():
    ds = pds.dataset(REPO_ROOT/"data_v2"/"snapshots", format="parquet", partitioning="hive")
    t = ds.to_table(columns=["match_id","received_at_ns","game_time_sec","league_id",
                              "radiant_lead","radiant_score","dire_score","data_source"],
                    filter=pc.field("data_source")=="top_live")
    snaps = defaultdict(list)
    for i in range(t.num_rows):
        snaps[t["match_id"][i].as_py()].append({
            "ns": t["received_at_ns"][i].as_py(),
            "gt": t["game_time_sec"][i].as_py(),
            "rl": t["radiant_lead"][i].as_py() or 0,
            "rs": t["radiant_score"][i].as_py() or 0,
            "ds": t["dire_score"][i].as_py() or 0,
            "league": t["league_id"][i].as_py(),
        })
    for mid in snaps: snaps[mid].sort(key=lambda x: x["ns"])
    return snaps


def load_book(tokens):
    ds = pds.dataset(REPO_ROOT/"data_v2"/"book_ticks", format="parquet", partitioning="hive")
    t = ds.to_table(columns=["asset_id","received_at_ns","mid","spread","ask_size","bid_size"],
                    filter=pc.is_in(pc.field("asset_id"), pa.array(list(tokens))))
    by = defaultdict(list)
    for i in range(t.num_rows):
        a = t["asset_id"][i].as_py()
        m = t["mid"][i].as_py()
        if m is None: continue
        by[a].append((t["received_at_ns"][i].as_py(), m,
                      t["spread"][i].as_py(),
                      t["ask_size"][i].as_py(),
                      t["bid_size"][i].as_py()))
    for a in by: by[a].sort()
    return {a: list(zip(*by[a])) for a in by}


def book_at(book, asset, ns):
    arr = book.get(asset)
    if not arr: return None
    times, mids, spreads, asks, bids = arr
    i = bisect.bisect_right(times, ns) - 1
    if i < 0: return None
    return {"mid": mids[i], "spread": spreads[i], "ask_size": asks[i], "bid_size": bids[i]}


# ---------- build sample dataset ----------
def build_samples():
    """One row per snapshot pair (3-snap window). Each row = features + 4 forward labels."""
    m2info = load_mapping()
    snaps = load_snapshots()
    joinable = [mid for mid in snaps if mid in m2info]
    book = load_book({m2info[mid]["yes"] for mid in joinable})
    joined = [mid for mid in joinable if m2info[mid]["yes"] in book]

    samples = []
    for mid in joined:
        info = m2info[mid]
        sign = 1 if info["side"]=="normal" else -1
        tok = info["yes"]
        srows = snaps[mid]
        if len(srows) < 4: continue
        # Pre-game mid: first available book tick (anchor before game progress matters)
        first_book = book[tok]
        pregame_mid = first_book[1][0] if first_book[1] else 0.5
        for i in range(3, len(srows)):
            cur = srows[i]
            prev = srows[i-1]
            prev3 = srows[i-3]
            if cur["gt"] is None or cur["gt"] < 300: continue
            gap = (cur["ns"] - prev["ns"]) / 1e9
            if gap > 60: continue  # don't use stale-prev features
            gap3 = (cur["ns"] - prev3["ns"]) / 1e9

            # Δ features (signed by YES-team-favor direction)
            d_lead_1 = (cur["rl"] - prev["rl"]) * sign
            d_lead_3 = (cur["rl"] - prev3["rl"]) * sign
            d_kill_1 = ((cur["rs"] - prev["rs"]) - (cur["ds"] - prev["ds"])) * sign
            d_kill_3 = ((cur["rs"] - prev3["rs"]) - (cur["ds"] - prev3["ds"])) * sign

            # Current state
            cur_lead_yes = cur["rl"] * sign
            cur_score_yes = (cur["rs"] if sign == 1 else cur["ds"])
            cur_score_no = (cur["ds"] if sign == 1 else cur["rs"])

            # Book state at snapshot
            b0 = book_at(book, tok, cur["ns"])
            if b0 is None: continue
            mid0 = b0["mid"]
            if not (0.05 < mid0 < 0.95): continue   # exclude near-terminal

            # Book state 30s, 60s earlier (recent book momentum)
            b_prev30 = book_at(book, tok, cur["ns"] - 30*1_000_000_000)
            book_30s_chg = (mid0 - b_prev30["mid"]) if b_prev30 else None

            # Forward labels (signed in direction of d_lead_1)
            direction = 1 if d_lead_1 > 0 else (-1 if d_lead_1 < 0 else 0)
            if direction == 0: continue  # need a direction to sign the label
            labels = {}
            for h in [5, 10, 30, 60, 120]:
                bh = book_at(book, tok, cur["ns"] + h*1_000_000_000)
                labels[h] = (bh["mid"] - mid0) * direction if bh else None
            if labels[30] is None: continue

            # Pre-game positioning, signed by direction
            pregame_signed = (pregame_mid - 0.5) * direction

            samples.append({
                "match_id": mid, "ns": cur["ns"], "league": cur["league"],
                "gt": cur["gt"],
                "gap1": gap, "gap3": gap3,
                "d_lead_1": d_lead_1, "d_lead_3": d_lead_3,
                "d_kill_1": d_kill_1, "d_kill_3": d_kill_3,
                "cur_lead_yes": cur_lead_yes,
                "cur_score_total": cur_score_yes + cur_score_no,
                "cur_score_diff_yes": cur_score_yes - cur_score_no,
                "mid0": mid0, "spread0": b0["spread"], "ask0": b0["ask_size"], "bid0": b0["bid_size"],
                "book_imbalance": (b0["ask_size"] / (b0["ask_size"] + b0["bid_size"])) if (b0["ask_size"] is not None and b0["bid_size"] is not None and (b0["ask_size"]+b0["bid_size"])>0) else None,
                "book_30s_chg": book_30s_chg,
                "pregame_mid": pregame_mid, "pregame_signed": pregame_signed,
                "direction": direction,
                "labels": labels,
            })
    return samples


# ---------- analysis utilities ----------
def correlation(xs, ys):
    """Pearson correlation. Returns 0 if degenerate."""
    n = len(xs)
    if n < 5: return 0.0
    mx, my = sum(xs)/n, sum(ys)/n
    cov = sum((x-mx)*(y-my) for x,y in zip(xs,ys))
    vx = sum((x-mx)**2 for x in xs)
    vy = sum((y-my)**2 for y in ys)
    if vx <= 0 or vy <= 0: return 0.0
    return cov / math.sqrt(vx * vy)


def pearson_per_feature(samples, label_horizon=30):
    """Compute Pearson correlation of each feature against the forward label."""
    features = ["d_lead_1","d_lead_3","d_kill_1","d_kill_3",
                "cur_lead_yes","cur_score_total","cur_score_diff_yes",
                "mid0","spread0","ask0","bid0","book_imbalance","book_30s_chg",
                "pregame_mid","pregame_signed","gt"]
    print(f"\n{'feature':<22} {'corr':>8} {'n':>5} {'|corr|':>7}")
    rows = []
    for f in features:
        xs, ys = [], []
        for s in samples:
            v = s.get(f)
            label = s["labels"].get(label_horizon)
            if v is None or label is None: continue
            xs.append(v); ys.append(label)
        c = correlation(xs, ys)
        rows.append((f, c, len(xs)))
    # Sort by |correlation|
    rows.sort(key=lambda r: -abs(r[1]))
    for f, c, n in rows:
        bar = "█" * min(20, int(abs(c)*100))
        print(f"  {f:<22} {c:+.4f} {n:>5} {bar}")


def threshold_analysis(samples, feature, label_horizon=30,
                       thresholds=None, label="feature"):
    """For a single feature, show forward-label stats by threshold bucket."""
    vals = [(s[feature], s["labels"][label_horizon]) for s in samples
            if s.get(feature) is not None and s["labels"].get(label_horizon) is not None]
    if not vals: return
    vals.sort()
    if thresholds is None:
        # Use quintile-derived thresholds
        n = len(vals)
        thresholds = [vals[n//5][0], vals[2*n//5][0], vals[3*n//5][0], vals[4*n//5][0]]

    print(f"\n  {label} | n={len(vals)}")
    edges = [-1e18] + list(thresholds) + [1e18]
    for lo, hi in zip(edges[:-1], edges[1:]):
        sub = [y for x, y in vals if lo <= x < hi]
        if not sub: continue
        mean = sum(sub)/len(sub)
        wins = sum(1 for y in sub if y > 0)
        med = sorted(sub)[len(sub)//2]
        print(f"    [{lo:>+10.2f}, {hi:>+10.2f}) n={len(sub):>4} "
              f"mean_d_mid={mean:>+7.4f} win={100*wins/len(sub):>4.0f}% med={med:>+7.4f}")


def continuous_strategy(samples, *, mid_lo=0.30, mid_hi=0.85,
                        require_d_lead_min=1500, hold=30,
                        gating_fn=None):
    """Trade rule: for every snapshot that meets the gates, "trade" in the
    direction of d_lead_1. Returns the same pnl shape as the event-detector
    backtest for comparison."""
    NOTIONAL = 5.0
    COST = 0.005
    trades = []
    for s in samples:
        if not (mid_lo <= s["mid0"] <= mid_hi): continue
        if abs(s["d_lead_1"]) < require_d_lead_min: continue
        if gating_fn is not None and not gating_fn(s): continue
        dm = s["labels"].get(hold)
        if dm is None: continue
        # pnl = ((dm * direction - cost) / ref) * notional  with direction already in sign of label
        pnl = ((dm - COST) / s["mid0"]) * NOTIONAL
        trades.append({"mid": s["match_id"], "gt": s["gt"], "pnl": pnl, "s": s})
    return trades


def report(label, trades):
    if not trades:
        print(f"  {label}: 0 trades"); return
    pnls = sorted(t["pnl"] for t in trades)
    n = len(pnls); w = sum(1 for p in pnls if p>0)
    print(f"  {label:<58} n={n:>4} ${sum(pnls):+7.2f} ${sum(pnls)/n:+.3f}/t win={100*w/n:>3.0f}% med=${pnls[n//2]:+.3f}")


# ---------- main study ----------
def main():
    print("Building snapshot-pair sample set...")
    samples = build_samples()
    print(f"Samples: {len(samples):,} (snapshot pairs with a non-zero d_lead_1 and a +30s book label)")

    print("\n" + "="*78)
    print("A. SINGLE-FEATURE PEARSON CORRELATION WITH +30s FORWARD MID MOVE")
    print("="*78)
    pearson_per_feature(samples, 30)

    print("\n" + "="*78)
    print("B. TOP PREDICTOR — d_lead_1 — THRESHOLD ANALYSIS")
    print("="*78)
    threshold_analysis(samples, "d_lead_1", 30,
                       thresholds=[-5000, -2000, -500, 0, 500, 2000, 5000],
                       label="d_lead_1 (signed by direction-of-trade)")

    print("\n" + "="*78)
    print("C. SAME PREDICTOR AT DIFFERENT HORIZONS")
    print("="*78)
    for h in [5, 10, 30, 60, 120]:
        xs, ys = [], []
        for s in samples:
            label = s["labels"].get(h)
            if label is None: continue
            xs.append(s["d_lead_1"]); ys.append(label)
        c = correlation(xs, ys)
        print(f"  d_lead_1 → +{h:>3}s mid move: corr = {c:+.4f}  n={len(xs)}")

    print("\n" + "="*78)
    print("D. CONDITIONAL STRENGTH — does d_lead_1 predict more at certain ref prices?")
    print("="*78)
    for lo, hi, name in [(0.30,0.45,"low"), (0.45,0.60,"mid-low"), (0.60,0.75,"mid-high"),
                         (0.75,0.85,"high")]:
        sub = [s for s in samples if lo<=s["mid0"]<hi and s["labels"].get(30) is not None]
        if len(sub) < 30: continue
        c = correlation([s["d_lead_1"] for s in sub], [s["labels"][30] for s in sub])
        print(f"  ref [{lo:.2f}, {hi:.2f}) {name:<10} corr = {c:+.4f}  n={len(sub)}")

    print("\n" + "="*78)
    print("E. CONDITIONAL STRENGTH — by game phase")
    print("="*78)
    for lo, hi, name in [(0,1200,"<20m"),(1200,1800,"20-30m"),(1800,2400,"30-40m"),
                         (2400,3600,"40-60m"),(3600,99999,">60m")]:
        sub = [s for s in samples if lo<=s["gt"]<hi and s["labels"].get(30) is not None]
        if len(sub) < 30: continue
        c = correlation([s["d_lead_1"] for s in sub], [s["labels"][30] for s in sub])
        print(f"  game_time {name:<8} corr = {c:+.4f}  n={len(sub)}")

    print("\n" + "="*78)
    print("F. CONTINUOUS STRATEGY: trade at every snapshot meeting simple gates")
    print("="*78)
    print("  (vs current event-detector approach which requires kill_diff agreement)")
    print()
    # Sweep |d_lead_1| thresholds
    print("  |d_lead_1| threshold sweep (ref [0.30, 0.85], 30s hold):")
    for thresh in [500, 1000, 1500, 2000, 2500, 3000, 5000]:
        trades = continuous_strategy(samples, require_d_lead_min=thresh, hold=30)
        report(f"  >= {thresh}", trades)
    print()

    print("  Same with no kill_diff agreement check vs requiring kill agreement:")
    no_kill_req = continuous_strategy(samples, require_d_lead_min=1500, hold=30)
    report("  no kill_diff agreement (continuous)", no_kill_req)
    with_kill_req = continuous_strategy(
        samples, require_d_lead_min=1500, hold=30,
        gating_fn=lambda s: (s["d_kill_1"] > 0 and s["d_lead_1"] > 0) or
                            (s["d_kill_1"] < 0 and s["d_lead_1"] < 0),
    )
    report("  WITH kill_diff agreement (event-detector style)", with_kill_req)

    print()
    print("  Optimal layered gates:")
    rules = [
        ("base: d_lead_1>=1500, ref [0.30,0.85]",
         lambda s: True),
        ("+ kill_diff agree",
         lambda s: (s["d_kill_1"] > 0) == (s["d_lead_1"] > 0)),
        ("+ phase mask (15-45m only)",
         lambda s: 900 <= s["gt"] < 2700),
        ("+ book imbalance NOT in [0.2, 0.4)",
         lambda s: s["book_imbalance"] is None or not (0.2 <= s["book_imbalance"] < 0.4)),
        ("+ pregame_signed >= 0 (signal aligns with narrative)",
         lambda s: s["pregame_signed"] >= 0),
        ("+ |cur_lead_yes| < 10000 (game not yet decided)",
         lambda s: abs(s["cur_lead_yes"]) < 10000),
    ]
    accumulated = []
    for label, fn in rules:
        accumulated.append(fn)
        combined = lambda s, fns=list(accumulated): all(f(s) for f in fns)
        t = continuous_strategy(samples, require_d_lead_min=1500, hold=30, gating_fn=combined)
        report(label, t)

    print("\n" + "="*78)
    print("G. HOLDING-PERIOD SWEEP for the best layered gate")
    print("="*78)
    def best_gate(s):
        return ((s["d_kill_1"] > 0) == (s["d_lead_1"] > 0)
                and 900 <= s["gt"] < 2700
                and (s["book_imbalance"] is None or not (0.2 <= s["book_imbalance"] < 0.4))
                and abs(s["cur_lead_yes"]) < 10000)
    for h in [5, 10, 30, 60, 120]:
        t = continuous_strategy(samples, require_d_lead_min=1500, hold=h, gating_fn=best_gate)
        report(f"  hold {h:>3}s", t)


if __name__ == "__main__":
    main()
