"""Mine logs/trade_dataset.csv (instant, no reload) for the edge pattern. Scans every GetTopLive
feature for where hold-to-settle return concentrates, ranks features by correlation, and greedily
searches for the best 1-2 feature gate. Note: rows are ~45s apart so multiple per match share an
outcome (overlap) -> treat as DISCOVERY, and the per-match collapse at the end is the honest check."""
import csv, math, sys
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "logs" / "trade_dataset.csv"
FEATS = ["edge", "fair", "ask", "lead", "gt", "kill_lead", "slope5", "gold_sw60", "kill_sw60"]


def main():
    rows = list(csv.DictReader(open(P)))
    for r in rows:
        for k in r:
            r[k] = float(r[k]) if k != "match_id" else r[k]
    n = len(rows)
    ret = [r["ret"] for r in rows]
    won = [r["won"] for r in rows]
    mret = sum(ret) / n
    print(f"=== EDGE MINING  (n={n} candidate rows, {len(set(r['match_id'] for r in rows))} matches) ===")
    print(f"  baseline: mean ret {mret:+.3f}  win {100*sum(won)/n:.0f}%  mean ask {sum(r['ask'] for r in rows)/n:.2f}\n")

    def corr(a, b):
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        va = sum((x - ma) ** 2 for x in a); vb = sum((x - mb) ** 2 for x in b)
        if va == 0 or vb == 0:
            return 0.0
        return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)

    print("1. FEATURE -> RETURN correlation (which GetTopLive features predict profit):")
    cs = sorted(((corr([r[f] for r in rows], ret), f) for f in FEATS), key=lambda x: -abs(x[0]))
    for c, f in cs:
        print(f"     {f:10} corr(ret) = {c:+.3f}")
    print()

    print("2. RETURN by feature quartile (mean ret | win% | n):")
    for f in FEATS:
        vals = sorted(r[f] for r in rows)
        qs = [vals[int(q * n)] for q in (0.25, 0.5, 0.75)]
        bnd = [(-1e18, qs[0]), (qs[0], qs[1]), (qs[1], qs[2]), (qs[2], 1e18)]
        cells = []
        for lo, hi in bnd:
            g = [r for r in rows if lo <= r[f] < hi] or [r for r in rows if r[f] == hi]
            if not g:
                cells.append("  -  "); continue
            cells.append(f"{sum(x['ret'] for x in g)/len(g):+.2f}/{100*sum(x['won'] for x in g)/len(g):.0f}%")
        print(f"     {f:10} Q1..Q4: " + "  ".join(f"{c:>11}" for c in cells))
    print()

    print("3. GREEDY best 1-feature gate (>= or <= threshold, maximize mean ret, n>=40):")
    best = []
    for f in FEATS:
        vals = sorted(set(round(r[f], 3) for r in rows))
        for t in vals:
            for op, lab in ((">=", lambda r, t=t, f=f: r[f] >= t), ("<=", lambda r, t=t, f=f: r[f] <= t)):
                g = [r for r in rows if lab(r)]
                if len(g) < 40:
                    continue
                m = sum(x["ret"] for x in g) / len(g)
                best.append((m, f, op, t, len(g), 100 * sum(x["won"] for x in g) / len(g)))
    best.sort(reverse=True)
    for m, f, op, t, ng, wr in best[:6]:
        print(f"     {f} {op} {t:<7g} -> ret {m:+.3f}  win {wr:.0f}%  n={ng}")
    print()

    print("4. GREEDY best 2-feature gate (n>=40):")
    two = []
    cand = [(f, op, t) for m, f, op, t, ng, wr in best[:12]]
    for i in range(len(cand)):
        for k in range(i + 1, len(cand)):
            (f1, o1, t1), (f2, o2, t2) = cand[i], cand[k]
            if f1 == f2:
                continue
            def ok(r, f1=f1, o1=o1, t1=t1, f2=f2, o2=o2, t2=t2):
                a = r[f1] >= t1 if o1 == ">=" else r[f1] <= t1
                b = r[f2] >= t2 if o2 == ">=" else r[f2] <= t2
                return a and b
            g = [r for r in rows if ok(r)]
            if len(g) < 40:
                continue
            two.append((sum(x["ret"] for x in g) / len(g), f1, o1, t1, f2, o2, t2, len(g),
                        100 * sum(x["won"] for x in g) / len(g)))
    two.sort(reverse=True)
    for m, f1, o1, t1, f2, o2, t2, ng, wr in two[:6]:
        print(f"     {f1}{o1}{t1:g} & {f2}{o2}{t2:g} -> ret {m:+.3f}  win {wr:.0f}%  n={ng}")
    print()

    # 5. honest per-match collapse (one row per match = first edge>=0.10 entry) to kill overlap inflation
    bym = {}
    for r in sorted(rows, key=lambda r: r["gt"]):
        if r["edge"] >= 0.10 and r["fair"] >= 0.70 and r["match_id"] not in bym:
            bym[r["match_id"]] = r
    if bym:
        g = list(bym.values())
        print(f"5. PER-MATCH (1 trade/match, current gate fair>=0.70 edge>=0.10): "
              f"n={len(g)}  ret {sum(x['ret'] for x in g)/len(g):+.3f}  win {100*sum(x['won'] for x in g)/len(g):.0f}%")


if __name__ == "__main__":
    main()
