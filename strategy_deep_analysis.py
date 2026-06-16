"""
strategy_deep_analysis.py — Comprehensive strategy analysis for the Dota 2 Polymarket bot.

Run:  python strategy_deep_analysis.py
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median, stdev

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
LOG_DIR = Path("logs")
SIGNALS_PATH      = LOG_DIR / "signals.csv"
LIVE_ATT_PATH     = LOG_DIR / "live_attempts.csv"
MARKOUTS_PATH     = LOG_DIR / "signal_markouts.csv"
DOTA_EV_PATH      = LOG_DIR / "dota_events.csv"
BOOK_EV_PATH      = LOG_DIR / "book_events.csv"
BOOK_MV_PATH      = LOG_DIR / "book_moves.csv"
LATENCY_PATH      = LOG_DIR / "latency.csv"
SOURCE_DELAY_PATH = LOG_DIR / "source_delay.csv"
SHADOW_PATH       = LOG_DIR / "shadow_trades.csv"
SNAPSHOTS_PATH    = LOG_DIR / "raw_snapshots.csv"

# Thresholds (from .env / config for reference)
CUR_MIN_EDGE   = 0.005
CUR_MIN_LAG    = 0.08
CUR_MAX_SPREAD = 0.12
CUR_MIN_QUALITY= 0.35
MIN_ASK_SIZE   = 5.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load(path: Path, **kw) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, low_memory=False, **kw)
        return df
    except Exception as e:
        print(f"  [warn] could not load {path}: {e}")
        return pd.DataFrame()


def num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce") if col in df.columns else pd.Series(dtype=float)


def pct_bar(p: float, width: int = 20) -> str:
    filled = round(p * width)
    return "[" + "#" * filled + "." * (width - filled) + f"] {p:5.1%}"


def hdr(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def sub(title: str) -> None:
    print(f"\n--- {title} ---")


def tbl(rows: list[tuple], headers: list[str], col_widths: list[int] | None = None) -> None:
    if not rows:
        print("  (no data)")
        return
    if col_widths is None:
        # auto-size
        col_widths = [max(len(str(headers[i])), max(len(str(r[i])) for r in rows)) + 2
                      for i in range(len(headers))]
    header = "  " + "".join(str(h).ljust(w) for h, w in zip(headers, col_widths))
    sep    = "  " + "-" * (sum(col_widths))
    print(header)
    print(sep)
    for row in rows:
        print("  " + "".join(str(v).ljust(w) for v, w in zip(row, col_widths)))


def fmt_f(v, fmt=".4f"):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "n/a"
    return format(float(v), fmt)


def fmt_pct(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "n/a"
    return f"{float(v):.1%}"


def percentile_row(series: pd.Series, label: str) -> tuple:
    s = series.dropna()
    if s.empty:
        return (label, "n/a", "n/a", "n/a", "n/a", "n/a", "n/a", "n/a")
    return (
        label,
        fmt_f(s.min(), ".4f"),
        fmt_f(s.quantile(0.10), ".4f"),
        fmt_f(s.quantile(0.25), ".4f"),
        fmt_f(s.median(), ".4f"),
        fmt_f(s.quantile(0.75), ".4f"),
        fmt_f(s.quantile(0.90), ".4f"),
        fmt_f(s.max(), ".4f"),
    )


# ---------------------------------------------------------------------------
# SECTION 1 — Data Inventory
# ---------------------------------------------------------------------------

def section_inventory() -> None:
    hdr("1. DATA INVENTORY")
    files = [
        ("signals.csv",       SIGNALS_PATH),
        ("live_attempts.csv", LIVE_ATT_PATH),
        ("signal_markouts.csv", MARKOUTS_PATH),
        ("dota_events.csv",   DOTA_EV_PATH),
        ("book_events.csv",   BOOK_EV_PATH),
        ("book_moves.csv",    BOOK_MV_PATH),
        ("latency.csv",       LATENCY_PATH),
        ("source_delay.csv",  SOURCE_DELAY_PATH),
        ("shadow_trades.csv", SHADOW_PATH),
        ("raw_snapshots.csv", SNAPSHOTS_PATH),
    ]
    rows = []
    for name, path in files:
        if not path.exists():
            rows.append((name, "MISSING", "-", "-", "-"))
            continue
        size_kb = path.stat().st_size / 1024
        df = load(path, nrows=5)
        total = sum(1 for _ in open(path, encoding="utf-8")) - 1  # subtract header
        ts_col = next((c for c in ("timestamp_utc", "received_at_utc") if c in df.columns), None)
        date_range = "-"
        if ts_col:
            full = load(path)
            ts = pd.to_datetime(full[ts_col], errors="coerce", utc=True)
            ts = ts.dropna()
            if not ts.empty:
                date_range = f"{ts.min().strftime('%m-%d %H:%M')} → {ts.max().strftime('%m-%d %H:%M')}"
        rows.append((name, f"{total:,} rows", f"{size_kb:.0f} KB", len(df.columns), date_range))

    tbl(rows, ["File", "Rows", "Size", "Cols", "Date Range"],
        [26, 12, 10, 6, 32])


# ---------------------------------------------------------------------------
# SECTION 2 — Signal Funnel
# ---------------------------------------------------------------------------

def section_signal_funnel(sig: pd.DataFrame) -> None:
    hdr("2. SIGNAL FUNNEL")
    if sig.empty:
        print("  no signal data"); return

    sig["executable_edge"] = num(sig, "executable_edge")
    sig["lag"]             = num(sig, "lag")
    sig["steam_age_ms"]    = num(sig, "steam_age_ms")
    sig["book_age_ms"]     = num(sig, "book_age_ms")
    sig["event_quality"]   = num(sig, "event_quality")
    sig["game_time_sec"]   = num(sig, "game_time_sec")

    n = len(sig)
    decisions = sig["decision"].value_counts()
    traded = decisions.get("paper_buy_yes", 0) + decisions.get("live_buy", 0)

    sub("Overall Funnel")
    print(f"  Total signals evaluated : {n}")
    for dec, cnt in decisions.items():
        pct = cnt / n
        print(f"  {dec:<30} {cnt:>4}  {pct_bar(pct)}")

    sub("Skip Reason Pareto")
    skipped = sig[sig["decision"] == "skip"]
    skip_counts = skipped["skip_reason"].value_counts()

    # Classify
    threshold_controllable = {
        "edge_too_small", "lag_too_small", "spread_too_wide",
        "insufficient_ask_size", "min_event_quality", "missing_cadence_event_schema",
    }
    structural = {
        "already_repriced", "steam_stale", "book_stale", "missing_book",
        "steam_contradicts", "opposing_book_stale", "no_exec_ask",
        "chasing_terminal_price", "priced_out_high_ground_stomp",
    }
    rows = []
    for reason, cnt in skip_counts.items():
        pct = cnt / len(skipped) if len(skipped) else 0
        category = "🔧 tunable" if reason in threshold_controllable else (
                   "🏗️  structural" if reason in structural else "❓ other")
        rows.append((reason, cnt, fmt_pct(pct), category))
    tbl(rows, ["Skip Reason", "Count", "% of Skips", "Category"],
        [36, 7, 12, 14])

    n_tunable   = skip_counts[skip_counts.index.isin(threshold_controllable)].sum()
    n_structural= skip_counts[skip_counts.index.isin(structural)].sum()
    print(f"\n  Tunable   blocks: {n_tunable} ({n_tunable/len(skipped):.1%})"  if len(skipped) else "")
    print(f"  Structural blocks: {n_structural} ({n_structural/len(skipped):.1%})" if len(skipped) else "")

    sub("Per-Event-Type Breakdown")
    rows = []
    for et, grp in sig.groupby("event_type"):
        n_et  = len(grp)
        n_tr  = (grp["decision"] == "paper_buy_yes").sum()
        tr_rt = n_tr / n_et if n_et else 0
        avg_e = grp["executable_edge"].mean()
        avg_l = grp["lag"].mean()
        top_sk = grp[grp["decision"] == "skip"]["skip_reason"].mode()
        top_sk = top_sk.iloc[0] if not top_sk.empty else "-"
        rows.append((et[:30], n_et, n_tr, fmt_pct(tr_rt), fmt_f(avg_e), fmt_f(avg_l), top_sk[:22]))
    rows.sort(key=lambda r: r[1], reverse=True)
    tbl(rows, ["Event Type", "N", "Traded", "Trade%", "AvgEdge", "AvgLag", "TopSkip"],
        [32, 5, 7, 8, 9, 9, 24])

    sub("Edge Distribution for 'edge_too_small' Skips")
    edge_skip = skipped[skipped["skip_reason"] == "edge_too_small"]["executable_edge"].dropna()
    if edge_skip.empty:
        print("  no edge_too_small skips")
    else:
        bins = [-1, 0, 0.001, 0.002, 0.003, 0.004, 0.005, 0.007, 0.010, 1]
        labels = ["<0","0-0.001","0.001-0.002","0.002-0.003","0.003-0.004",
                  "0.004-0.005 (threshold)","0.005-0.007","0.007-0.010",">0.010"]
        cuts = pd.cut(edge_skip, bins=bins, labels=labels)
        vc = cuts.value_counts().sort_index()
        for lbl, cnt in vc.items():
            print(f"  edge {str(lbl):<28}  {cnt:>3}  {pct_bar(cnt/len(edge_skip))}")

    sub("Lag Distribution for 'lag_too_small' Skips")
    lag_skip = skipped[skipped["skip_reason"] == "lag_too_small"]["lag"].dropna()
    if lag_skip.empty:
        print("  no lag_too_small skips")
    else:
        bins = [-99, 0, 0.02, 0.04, 0.06, 0.08, 0.10, 99]
        labels = ["<0","0-0.02","0.02-0.04","0.04-0.06","0.06-0.08 (near threshold)","0.08-0.10",">0.10"]
        cuts = pd.cut(lag_skip, bins=bins, labels=labels)
        vc = cuts.value_counts().sort_index()
        for lbl, cnt in vc.items():
            print(f"  lag {str(lbl):<34}  {cnt:>3}  {pct_bar(cnt/len(lag_skip))}")


# ---------------------------------------------------------------------------
# SECTION 3 — Edge Calibration & Predictive Power
# ---------------------------------------------------------------------------

def section_edge_calibration(mko: pd.DataFrame, sig: pd.DataFrame) -> None:
    hdr("3. EDGE CALIBRATION & PREDICTIVE POWER")
    if mko.empty:
        print("  no markout data"); return

    mko = mko.copy()
    for c in ("markout_3s", "markout_10s", "markout_30s", "executable_edge",
              "edge_after_3s", "edge_after_10s", "edge_after_30s"):
        mko[c] = pd.to_numeric(mko[c], errors="coerce")

    sub("Overall Markout Distribution (all signals)")
    rows = []
    for col, label in [("markout_3s","3s"),("markout_10s","10s"),("markout_30s","30s")]:
        s = mko[col].dropna()
        if s.empty:
            continue
        win = (s > 0).sum()
        rows.append((label, len(s), fmt_f(s.mean()), fmt_f(s.median()),
                     fmt_pct(win/len(s)), fmt_f(s[s>0].mean() if win else None),
                     fmt_f(s[s<0].mean() if (s<0).any() else None)))
    tbl(rows, ["Window","N","Mean","Median","Win%","AvgWin","AvgLoss"],
        [8,6,8,8,8,10,10])

    sub("Calibration: Edge Bucket vs Actual Markout (executable_edge)")
    bins = [float('-inf'), 0, 0.005, 0.010, 0.020, 0.030, 0.050, float('inf')]
    labels = ["<0", "0–0.005", "0.005–0.010", "0.010–0.020", "0.020–0.030", "0.030–0.050", ">0.050"]
    mko["edge_bucket"] = pd.cut(mko["executable_edge"], bins=bins, labels=labels)
    rows = []
    for bucket, grp in mko.groupby("edge_bucket", observed=False):
        m3  = grp["markout_3s"].dropna()
        m30 = grp["markout_30s"].dropna()
        win = (m30 > 0).sum() if len(m30) else 0
        rows.append((str(bucket), len(grp),
                     fmt_f(grp["executable_edge"].mean()),
                     fmt_f(m3.mean() if len(m3) else None),
                     fmt_f(m30.mean() if len(m30) else None),
                     fmt_pct(win / len(m30) if len(m30) else None)))
    tbl(rows, ["Edge Bucket","N","AvgEdge","M@3s","M@30s","Win%@30s"],
        [16, 5, 9, 8, 8, 10])

    sub("Per-Event-Type: Predictive Power")
    rows = []
    for et, grp in mko.groupby("event_type"):
        m30 = grp["markout_30s"].dropna()
        if m30.empty:
            continue
        win = (m30 > 0).sum()
        sharpe = m30.mean() / m30.std() if m30.std() > 0 else float("nan")
        avg_e  = grp["executable_edge"].mean()
        rows.append((et[:30], len(grp), fmt_f(m30.mean()), fmt_f(m30.median()),
                     fmt_pct(win/len(m30)), fmt_f(sharpe), fmt_f(avg_e)))
    rows.sort(key=lambda r: float(r[2]) if r[2] != "n/a" else -999, reverse=True)
    tbl(rows, ["Event Type","N","Avg M@30s","Med M@30s","Win%@30s","Sharpe","AvgEdge"],
        [32, 5, 10, 10, 10, 8, 9])

    sub("Traded vs Skipped: Markout Comparison")
    for decision in ("paper_buy_yes", "skip"):
        grp = mko[mko["decision"] == decision]
        m30 = grp["markout_30s"].dropna()
        if m30.empty:
            continue
        win = (m30 > 0).sum()
        print(f"  [{decision}]  n={len(m30)}  "
              f"mean={fmt_f(m30.mean())}  median={fmt_f(m30.median())}  "
              f"win%={fmt_pct(win/len(m30))}")


# ---------------------------------------------------------------------------
# SECTION 4 — Live Execution Rejection Analysis
# ---------------------------------------------------------------------------

def section_live_rejections(att: pd.DataFrame) -> None:
    hdr("4. LIVE EXECUTION REJECTION ANALYSIS")
    if att.empty:
        print("  no live_attempts data"); return

    att = att.copy()
    for c in ("submitted_size_usd", "filled_size_usd", "avg_fill_price",
              "markout_3s", "markout_10s", "markout_30s"):
        att[c] = pd.to_numeric(att[c], errors="coerce")

    # Only look at submit-phase rows (not markout rows)
    submits = att[att["phase"] == "submit"] if "phase" in att.columns else att

    sub("Order Status Distribution")
    status_vc = submits["order_status"].value_counts()
    for status, cnt in status_vc.items():
        pct = cnt / len(submits)
        print(f"  {str(status):<35}  {cnt:>4}  {pct_bar(pct)}")

    sub("Rejection Reason Pareto")
    rejected = submits[submits["order_status"].str.contains("reject", na=False, case=False)]
    if not rejected.empty:
        rr = rejected["reason_if_rejected"].value_counts()
        rows = []
        for reason, cnt in rr.items():
            pct = cnt / len(rejected)
            # classify
            if reason in ("max_total_live_usd_reached", "max_open_positions_reached"):
                cat = "budget/risk gate"
            elif reason in ("missing_cadence_event_schema", "cadence_quality_too_low",
                             "min_event_quality_not_met"):
                cat = "data quality gate"
            elif reason in ("order_version_mismatch", "bad_request", "api_error"):
                cat = "API error (now fixed)"
            elif reason in ("edge_too_small", "lag_too_small", "spread_too_wide"):
                cat = "threshold gate"
            else:
                cat = "other"
            rows.append((str(reason)[:38], cnt, fmt_pct(pct), cat))
        tbl(rows, ["Reason", "N", "% of Rej.", "Category"],
            [40, 6, 10, 20])

    sub("Fill Rate Summary")
    filled = submits[submits["filled_size_usd"].fillna(0) > 0]
    total_sub = submits["submitted_size_usd"].sum()
    total_fill= submits["filled_size_usd"].sum()
    print(f"  Orders submitted   : {len(submits)}")
    print(f"  Orders with fill   : {len(filled)}")
    print(f"  Fill rate (orders) : {fmt_pct(len(filled)/len(submits) if len(submits) else 0)}")
    print(f"  USD submitted      : ${total_sub:.2f}")
    print(f"  USD filled         : ${total_fill:.2f}")
    print(f"  Fill rate (USD)    : {fmt_pct(total_fill/total_sub if total_sub else 0)}")

    sub("Markout for Orders That Were Filled")
    if not filled.empty:
        for col, lbl in [("markout_3s","3s"),("markout_10s","10s"),("markout_30s","30s")]:
            m = filled[col].dropna()
            if m.empty: continue
            win = (m > 0).sum()
            print(f"  {lbl}: n={len(m)}  mean={fmt_f(m.mean())}  "
                  f"median={fmt_f(m.median())}  win%={fmt_pct(win/len(m))}")

    sub("Post-v2-Fix Projection (removing API errors from rejection count)")
    api_errors = {"order_version_mismatch", "bad_request", "api_error"}
    if "reason_if_rejected" in submits.columns:
        correctable = submits[submits["reason_if_rejected"].isin(api_errors)]
        remaining_bad = rejected[~rejected["reason_if_rejected"].isin(api_errors)]
        remaining_bad_budget = remaining_bad[
            remaining_bad["reason_if_rejected"] == "max_total_live_usd_reached"]
        print(f"  Orders blocked by API version errors  : {len(correctable)} (now fixed)")
        print(f"  Orders blocked by budget exhaustion   : {len(remaining_bad_budget)} (reset budget before run)")
        net_blockable = len(correctable) + len(remaining_bad_budget)
        print(f"  Projected fill rate after fixes       : "
              f"{fmt_pct((len(filled)+net_blockable)/len(submits) if len(submits) else 0)} (optimistic upper bound)")


# ---------------------------------------------------------------------------
# SECTION 5 — Book / Market Quality
# ---------------------------------------------------------------------------

def section_book_quality(bk: pd.DataFrame, sig: pd.DataFrame, bmv: pd.DataFrame) -> None:
    hdr("5. BOOK / MARKET QUALITY")

    if not bk.empty:
        bk = bk.copy()
        bk["spread"]   = pd.to_numeric(bk["spread"], errors="coerce")
        bk["ask_size"] = pd.to_numeric(bk["ask_size"], errors="coerce")
        bk["best_ask"] = pd.to_numeric(bk["best_ask"], errors="coerce")
        bk["best_bid"] = pd.to_numeric(bk["best_bid"], errors="coerce")

        sub("Spread Distribution (all book snapshots)")
        spreads = bk["spread"].dropna()
        headers = ["Metric","p10","p25","p50","p75","p90","p99","max"]
        row = percentile_row(spreads, f"spread (n={len(spreads):,})")
        tbl([row], headers, [26,8,8,8,8,8,8,8])
        pct_above_threshold = (spreads > CUR_MAX_SPREAD).mean()
        print(f"  % spread > MAX_SPREAD ({CUR_MAX_SPREAD})  :  {fmt_pct(pct_above_threshold)}")

        sub("Ask Liquidity — distribution of ask_size")
        ask_sizes = bk["ask_size"].dropna()
        row = percentile_row(ask_sizes, f"ask_size USD (n={len(ask_sizes):,})")
        tbl([row], ["Metric","p10","p25","p50","p75","p90","p99","max"],
            [26,8,8,8,8,8,8,8])
        pct_thin = (ask_sizes < MIN_ASK_SIZE).mean()
        print(f"  % ask_size < MIN_ASK_SIZE ({MIN_ASK_SIZE})  :  {fmt_pct(pct_thin)}")

    if not sig.empty:
        sig = sig.copy()
        sig["book_age_at_signal_ms"] = pd.to_numeric(sig["book_age_at_signal_ms"], errors="coerce")
        sig["book_age_ms"]           = pd.to_numeric(sig["book_age_ms"], errors="coerce")
        sig["spread"]                = pd.to_numeric(sig["spread"], errors="coerce")

        sub("Book Freshness at Signal Time")
        age_col = "book_age_at_signal_ms" if sig["book_age_at_signal_ms"].notna().any() else "book_age_ms"
        ages = sig[age_col].dropna()
        if not ages.empty:
            row = percentile_row(ages, f"book_age_ms (n={len(ages)})")
            tbl([row], ["Metric","p10","p25","p50","p75","p90","p99","max"],
                [26,8,8,8,9,9,9,9])

        sub("Spread at Signal Time by Market")
        rows = []
        for mkt, grp in sig.groupby("market_name"):
            sp = grp["spread"].dropna()
            if sp.empty: continue
            rows.append((mkt[:45], len(grp), fmt_f(sp.mean()), fmt_f(sp.median()),
                         fmt_pct((sp > CUR_MAX_SPREAD).mean())))
        rows.sort(key=lambda r: float(r[2]) if r[2] != "n/a" else 999, reverse=True)
        tbl(rows[:15], ["Market","Signals","AvgSpread","MedSpread","% Too Wide"],
            [47, 8, 11, 11, 12])

    if not bmv.empty:
        bmv = bmv.copy()
        bmv["magnitude"] = pd.to_numeric(bmv["magnitude"], errors="coerce")

        sub("BOOK_MOVE Signal Quality")
        row = percentile_row(bmv["magnitude"].dropna(), f"move magnitude (n={len(bmv)})")
        tbl([row], ["Metric","p10","p25","p50","p75","p90","p99","max"],
            [26,8,8,8,8,8,8,8])
        if "steam_corroborated" in bmv.columns:
            corr = bmv["steam_corroborated"].value_counts()
            print(f"  Steam-corroborated : {corr.get('True', corr.get(True, 0))}/{len(bmv)}")
        if "traded" in bmv.columns:
            traded_vc = bmv["traded"].value_counts()
            print(f"  Resulted in trade  : {traded_vc.get('True', traded_vc.get(True, 0))}/{len(bmv)}")
            if "trade_skip_reason" in bmv.columns:
                skip_vc = bmv[~bmv["traded"].isin([True, "True"])]["trade_skip_reason"].value_counts().head(8)
                if not skip_vc.empty:
                    print("  Book-move skip reasons:")
                    for r, c in skip_vc.items():
                        print(f"    {str(r):<36} {c}")


# ---------------------------------------------------------------------------
# SECTION 6 — Event Quality Analysis
# ---------------------------------------------------------------------------

def section_event_quality(ev: pd.DataFrame, sig: pd.DataFrame) -> None:
    hdr("6. EVENT QUALITY ANALYSIS")
    if ev.empty:
        print("  no dota_events data"); return

    ev = ev.copy()
    for c in ("event_quality", "base_pressure_score", "fight_pressure_score",
              "economic_pressure_score", "conversion_score", "game_time_sec",
              "networth_delta", "kill_diff_delta", "event_confidence"):
        ev[c] = pd.to_numeric(ev[c], errors="coerce")

    sub("Event Type Frequency & Quality")
    rows = []
    for et, grp in ev.groupby("event_type"):
        eq = grp["event_quality"].dropna()
        rows.append((et[:32], len(grp),
                     grp["event_tier"].mode().iloc[0] if "event_tier" in grp.columns and not grp["event_tier"].mode().empty else "-",
                     fmt_f(eq.min()) if not eq.empty else "n/a",
                     fmt_f(eq.median()) if not eq.empty else "n/a",
                     fmt_f(eq.max()) if not eq.empty else "n/a"))
    rows.sort(key=lambda r: r[1], reverse=True)
    tbl(rows, ["Event Type","N","Tier","MinQ","MedQ","MaxQ"],
        [34, 5, 6, 8, 8, 8])

    sub("Source Cadence Quality Distribution")
    if "source_cadence_quality" in ev.columns:
        vc = ev["source_cadence_quality"].value_counts()
        ALLOWED = {"direct", "normal"}
        for qual, cnt in vc.items():
            marker = "✓ allowed" if qual in ALLOWED else "✗ blocked"
            pct = cnt / len(ev)
            print(f"  {str(qual):<15}  {cnt:>4}  {pct_bar(pct)}  {marker}")

    sub("Pressure Scores by Event Type")
    pressure_cols = ["base_pressure_score", "fight_pressure_score",
                     "economic_pressure_score", "conversion_score"]
    rows = []
    for et, grp in ev.groupby("event_type"):
        row_vals = [et[:28]]
        for col in pressure_cols:
            s = grp[col].dropna()
            row_vals.append(fmt_f(s.mean()) if not s.empty else "n/a")
        rows.append(tuple(row_vals))
    rows.sort(key=lambda r: r[0])
    tbl(rows, ["Event Type","BasePressure","FightPressure","EconPressure","ConvScore"],
        [30, 13, 13, 13, 10])

    sub("Events by Game Phase (game_time_sec)")
    bins = [0, 600, 1200, 1800, 2700, 99999]
    labels = ["0-10min","10-20min","20-30min","30-45min",">45min"]
    ev["phase"] = pd.cut(ev["game_time_sec"], bins=bins, labels=labels)
    phase_vc = ev["phase"].value_counts().sort_index()
    for phase, cnt in phase_vc.items():
        print(f"  {str(phase):<12}  {cnt:>4}  {pct_bar(cnt/len(ev))}")

    if not sig.empty and "event_type" in sig.columns:
        sub("Events Without Corresponding Signal (potential mismatch)")
        sig_events = set(sig["event_type"].dropna().unique()) if not sig.empty else set()
        ev_events  = set(ev["event_type"].dropna().unique())
        no_signal  = ev_events - sig_events
        if no_signal:
            print(f"  Event types detected but never producing a signal: {sorted(no_signal)}")
        else:
            print("  All event types produced at least one signal evaluation.")


# ---------------------------------------------------------------------------
# SECTION 7 — Latency & Pipeline Performance
# ---------------------------------------------------------------------------

def section_latency(lat: pd.DataFrame, src: pd.DataFrame, snap: pd.DataFrame) -> None:
    hdr("7. LATENCY & PIPELINE PERFORMANCE")

    if not lat.empty:
        lat = lat.copy()
        for c in ("event_detection_latency_ms", "signal_eval_latency_ms",
                  "steam_source_update_age_sec", "stream_delay_s"):
            lat[c] = pd.to_numeric(lat[c], errors="coerce")

        sub("End-to-End Pipeline Latency (ms)")
        hdrs = ["Stage","p10","p25","p50","p75","p90","p99","max"]
        tbl([
            percentile_row(lat["event_detection_latency_ms"].dropna(), "event_detection_ms"),
            percentile_row(lat["signal_eval_latency_ms"].dropna(), "signal_eval_ms"),
        ], hdrs, [24,8,8,8,8,8,8,9])

        sub("Steam Source Freshness")
        row = percentile_row(lat["steam_source_update_age_sec"].dropna(), "source_update_age_sec")
        tbl([row], hdrs, [24,8,8,8,8,8,8,9])
        pct_stale = (lat["steam_source_update_age_sec"] > 120).mean()
        print(f"  % readings > 120s (stale threshold) : {fmt_pct(pct_stale)}")

        if "data_source" in lat.columns:
            sub("Signal Data Source Distribution")
            ds_vc = lat["data_source"].value_counts()
            for ds, cnt in ds_vc.items():
                print(f"  {str(ds):<20}  {cnt:>4}  {pct_bar(cnt/len(lat))}")

    if not src.empty:
        src = src.copy()
        for c in ["game_time_lag_sec"]:
            if c in src.columns:
                src[c] = pd.to_numeric(src[c], errors="coerce")

        sub("LiveLeague vs TopLive Source Delay")
        if "game_time_lag_sec" in src.columns:
            tbl([
                percentile_row(src["game_time_lag_sec"].dropna(), "game_time_lag_sec"),
            ], ["Metric","p10","p25","p50","p75","p90","p99","max"],
               [26,8,8,8,8,8,8,9])
        else:
            print("  (no game_time_lag_sec in source_delay log)")

    if not snap.empty:
        snap = snap.copy()
        snap["received_at_ns"] = pd.to_numeric(snap["received_at_ns"], errors="coerce")
        snap = snap.sort_values("received_at_ns")
        snap_by_match = snap.groupby("match_id")
        interval_ms_all = []
        for match_id, grp in snap_by_match:
            ns = grp["received_at_ns"].dropna().sort_values()
            if len(ns) > 1:
                gaps = ns.diff().dropna() / 1e6
                interval_ms_all.extend(gaps.tolist())
        if interval_ms_all:
            series = pd.Series(interval_ms_all)
            sub("Steam API Update Interval (ms per match)")
            tbl([percentile_row(series, f"update_interval_ms (n={len(series):,})")],
                ["Metric","p10","p25","p50","p75","p90","p99","max"],
                [26,8,8,8,8,8,8,9])


# ---------------------------------------------------------------------------
# SECTION 8 — Threshold Sensitivity Analysis
# ---------------------------------------------------------------------------

def section_threshold_sensitivity(mko: pd.DataFrame, sig: pd.DataFrame) -> None:
    hdr("8. THRESHOLD SENSITIVITY ANALYSIS")
    if mko.empty:
        print("  no markout data"); return

    mko = mko.copy()
    for c in ("executable_edge", "markout_30s"):
        mko[c] = pd.to_numeric(mko[c], errors="coerce")

    if not sig.empty:
        sig = sig.copy()
        sig["executable_edge"] = pd.to_numeric(sig["executable_edge"], errors="coerce")
        sig["lag"]             = pd.to_numeric(sig["lag"], errors="coerce")
        sig["spread"]          = pd.to_numeric(sig["spread"], errors="coerce")
        sig["event_quality"]   = pd.to_numeric(sig["event_quality"], errors="coerce")

    def sweep_markout(threshold_name: str, col: str, candidates: list[float],
                      data: pd.DataFrame, current_val: float, direction: str = "above") -> None:
        """For each threshold value, show signals that newly pass and their avg markout."""
        sub(f"{threshold_name} sweep (current: {current_val})")
        rows = []
        for thr in candidates:
            if direction == "above":
                subset = data[data[col] >= thr]
            else:  # below = max
                subset = data[data[col] <= thr]
            m30 = subset["markout_30s"].dropna()
            win = (m30 > 0).sum() if len(m30) else 0
            marker = "← current" if abs(thr - current_val) < 1e-6 else ""
            rows.append((fmt_f(thr, ".3f"), len(subset),
                         fmt_f(m30.mean()) if not m30.empty else "n/a",
                         fmt_pct(win / len(m30)) if len(m30) else "n/a",
                         marker))
        tbl(rows, ["Threshold","N_qualify","Avg M@30s","Win%@30s",""],
            [12, 12, 12, 12, 12])

    # Edge sweep on markouts (all signals including skipped)
    sweep_markout("MIN_EXECUTABLE_EDGE", "executable_edge",
                  [0.001, 0.002, 0.003, 0.004, 0.005, 0.007, 0.010],
                  mko, CUR_MIN_EDGE, direction="above")

    if not sig.empty:
        # Lag sweep on signals.csv
        sub(f"MIN_LAG sweep (current: {CUR_MIN_LAG})")
        rows = []
        for thr in [0.02, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10, 0.12]:
            subset = sig[sig["lag"].abs() >= thr]
            marker = "← current" if abs(thr - CUR_MIN_LAG) < 1e-6 else ""
            rows.append((fmt_f(thr, ".2f"), len(subset), marker))
        tbl(rows, ["Threshold","N_signals",""], [12,12,12])

        sub(f"MAX_SPREAD sweep (current: {CUR_MAX_SPREAD})")
        rows = []
        for thr in [0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.20]:
            subset_ok = sig[sig["spread"].fillna(999) <= thr]
            subset_blocked = sig[sig["spread"].fillna(999) > thr]
            marker = "← current" if abs(thr - CUR_MAX_SPREAD) < 1e-6 else ""
            rows.append((fmt_f(thr, ".2f"), len(subset_ok), len(subset_blocked), marker))
        tbl(rows, ["Threshold","N_pass","N_blocked",""], [12, 10, 11, 12])

        sub(f"LIVE_MIN_EVENT_QUALITY sweep (current: {CUR_MIN_QUALITY})")
        rows = []
        for thr in [0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60]:
            subset = sig[sig["event_quality"].fillna(0) >= thr]
            marker = "← current" if abs(thr - CUR_MIN_QUALITY) < 1e-6 else ""
            rows.append((fmt_f(thr, ".2f"), len(subset), marker))
        tbl(rows, ["Threshold","N_qualify",""], [12, 12, 12])


# ---------------------------------------------------------------------------
# SECTION 9 — Per-Event-Type Strategy Report
# ---------------------------------------------------------------------------

def section_strategy_report(ev: pd.DataFrame, sig: pd.DataFrame, mko: pd.DataFrame,
                              att: pd.DataFrame) -> None:
    hdr("9. PER-EVENT-TYPE STRATEGY REPORT")

    TRADE_EVENTS = {
        "BASE_PRESSURE_T3_COLLAPSE","BASE_PRESSURE_T4",
        "OBJECTIVE_CONVERSION_T3","OBJECTIVE_CONVERSION_T4",
        "POLL_COMEBACK_RECOVERY","POLL_FIGHT_SWING","POLL_KILL_BURST_CONFIRMED",
        "POLL_LATE_FIGHT_FLIP","POLL_LEAD_FLIP_WITH_KILLS",
        "POLL_MAJOR_COMEBACK_RECOVERY","POLL_STOMP_THROW_CONFIRMED",
        "POLL_ULTRA_LATE_FIGHT_FLIP","THRONE_EXPOSED","BOOK_MOVE",
    }

    # Build per-event table
    mko_num = mko.copy()
    for c in ("executable_edge", "markout_30s"):
        mko_num[c] = pd.to_numeric(mko_num[c], errors="coerce")

    rows = []
    all_events = set()
    if not ev.empty and "event_type" in ev.columns:
        all_events |= set(ev["event_type"].dropna())
    if not sig.empty and "event_type" in sig.columns:
        all_events |= set(sig["event_type"].dropna())
    if not mko.empty and "event_type" in mko.columns:
        all_events |= set(mko["event_type"].dropna())

    for et in sorted(all_events):
        n_det  = len(ev[ev["event_type"] == et]) if not ev.empty else 0
        n_sig  = len(sig[sig["event_type"] == et]) if not sig.empty else 0
        n_trad = len(sig[(sig["event_type"] == et) & (sig["decision"] == "paper_buy_yes")]) if not sig.empty else 0
        trad_rt= n_trad / n_sig if n_sig else 0

        mko_et = mko_num[mko_num["event_type"] == et]["markout_30s"].dropna()
        avg_m30 = mko_et.mean() if not mko_et.empty else float("nan")
        win_pct = (mko_et > 0).mean() if not mko_et.empty else float("nan")

        avg_e  = mko_num[mko_num["event_type"] == et]["executable_edge"].mean()

        # E[PnL per detection] = avg_markout * trade_rate * per_trade_size
        ev_pnl = avg_m30 * trad_rt * 5.0 if not np.isnan(avg_m30) else float("nan")

        top_skip = "-"
        if not sig.empty:
            s_et = sig[(sig["event_type"] == et) & (sig["decision"] == "skip")]["skip_reason"]
            if not s_et.empty:
                top_skip = s_et.mode().iloc[0]

        in_te = "✓" if et in TRADE_EVENTS else " "
        rows.append((in_te, et[:30], n_det, n_sig, n_trad, fmt_pct(trad_rt),
                     fmt_f(avg_e), fmt_f(avg_m30), fmt_pct(win_pct),
                     fmt_f(ev_pnl, ".4f"), top_skip[:22]))

    # Sort by E[PnL] descending (best first)
    def sort_key(r):
        v = r[9]
        try: return float(v)
        except: return -999

    rows.sort(key=sort_key, reverse=True)
    tbl(rows,
        ["✓","Event Type","Dets","Sigs","Trades","Trade%","AvgEdge","M@30s","Win%","E[PnL@$5]","TopSkip"],
        [3, 32, 6, 6, 7, 8, 9, 8, 7, 11, 24])

    print("\n  ✓ = event type in TRADE_EVENTS allowlist")
    print("  E[PnL@$5] = avg_markout_30s × trade_rate × $5 (per detection opportunity)")


# ---------------------------------------------------------------------------
# SECTION 10 — Recommendations
# ---------------------------------------------------------------------------

def section_recommendations(sig: pd.DataFrame, att: pd.DataFrame, mko: pd.DataFrame,
                              bk: pd.DataFrame, ev: pd.DataFrame) -> None:
    hdr("10. RECOMMENDATIONS SUMMARY")

    print()
    print("  Based on the analysis above, here are prioritized recommendations:\n")

    recs = []

    # --- Execution health ---
    if not att.empty:
        att_sub = att[att.get("phase", pd.Series(["submit"]*len(att))) == "submit"] if "phase" in att.columns else att
        att_sub = att_sub.copy()
        att_sub["reason_if_rejected"] = att_sub.get("reason_if_rejected", pd.Series(dtype=str))
        api_errors = att_sub["reason_if_rejected"].isin(
            ["order_version_mismatch","bad_request","api_error"]).sum()
        budget_blocks = (att_sub["reason_if_rejected"] == "max_total_live_usd_reached").sum()
        cadence_blocks= att_sub["reason_if_rejected"].str.contains("cadence", na=False).sum()

        if api_errors > 0:
            recs.append(("CRITICAL", "SDK version",
                         f"{api_errors} orders failed with order_version_mismatch. "
                         "py-clob-client-v2 migration DONE — restart bot to verify."))
        if budget_blocks > 0:
            recs.append(("HIGH", "Budget resets",
                         f"{budget_blocks} orders blocked by max_total_live_usd_reached. "
                         "These are caused by failed orders consuming budget. Consider incrementing "
                         "total_submitted_usd only after confirmed fill."))
        if cadence_blocks > 5:
            recs.append(("HIGH", "Cadence schema filter",
                         f"{cadence_blocks} orders blocked by missing_cadence_event_schema. "
                         "This is a data quality gate. BOOK_MOVE events may lack cadence metadata — "
                         "verify LIVE_REQUIRE_CADENCE_SCHEMA=false for BOOK_MOVE or fix event emission."))

    # --- Threshold tuning ---
    if not mko.empty:
        mko_num = mko.copy()
        mko_num["executable_edge"] = pd.to_numeric(mko_num["executable_edge"], errors="coerce")
        mko_num["markout_30s"]     = pd.to_numeric(mko_num["markout_30s"], errors="coerce")

        edge_skip_near = mko_num[
            (mko_num["decision"] == "skip") &
            (mko_num["executable_edge"].between(0.002, CUR_MIN_EDGE, inclusive="both"))
        ]
        if len(edge_skip_near) > 0:
            m30 = edge_skip_near["markout_30s"].dropna()
            win = (m30 > 0).mean() if len(m30) else 0
            recs.append(("MEDIUM", "MIN_EXECUTABLE_EDGE",
                         f"{len(edge_skip_near)} skipped signals had edge in [0.002–{CUR_MIN_EDGE}]. "
                         f"Their avg markout@30s={fmt_f(m30.mean())}, win%={fmt_pct(win)}. "
                         f"If win%>50% and markout>0, consider reducing to 0.003."))

    if not sig.empty:
        sig_num = sig.copy()
        sig_num["lag"] = pd.to_numeric(sig_num["lag"], errors="coerce")
        lag_near = sig_num[
            (sig_num["decision"] == "skip") &
            (sig_num["skip_reason"] == "lag_too_small") &
            (sig_num["lag"].abs().between(0.04, CUR_MIN_LAG, inclusive="both"))
        ]
        if len(lag_near) > 0:
            recs.append(("MEDIUM", "MIN_LAG",
                         f"{len(lag_near)} signals skipped with lag in [0.04–{CUR_MIN_LAG}]. "
                         f"If markouts are positive, consider reducing MIN_LAG to 0.05."))

    # --- Event type quality ---
    if not mko.empty and "event_type" in mko.columns:
        mko_num2 = mko.copy()
        mko_num2["markout_30s"] = pd.to_numeric(mko_num2["markout_30s"], errors="coerce")
        for et, grp in mko_num2.groupby("event_type"):
            m30 = grp["markout_30s"].dropna()
            if len(m30) >= 5 and m30.mean() < -0.01:
                recs.append(("MEDIUM", f"Disable {et}",
                             f"Avg markout@30s = {fmt_f(m30.mean())} across {len(m30)} signals. "
                             "Consistently negative → consider removing from TRADE_EVENTS."))

    # --- Book quality ---
    if not bk.empty:
        bk_num = bk.copy()
        bk_num["spread"] = pd.to_numeric(bk_num["spread"], errors="coerce")
        pct_wide = (bk_num["spread"] > CUR_MAX_SPREAD).mean()
        if pct_wide > 0.30:
            recs.append(("LOW", "Book spread",
                         f"{fmt_pct(pct_wide)} of book snapshots have spread > {CUR_MAX_SPREAD}. "
                         "Consider reducing MAX_SPREAD or deprioritizing thin markets."))

    # --- Latency ---
    recs.append(("INFO", "Valve update cadence",
                 "See section 7 for steam_source_update_age_sec. "
                 "If p90 > 10s, consider TopLive as primary (lower stream delay than LiveLeague)."))

    # Print
    priority_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    recs.sort(key=lambda r: priority_order.get(r[0], 99))

    for i, (priority, topic, desc) in enumerate(recs, 1):
        tag = f"[{priority}]"
        print(f"  {i:>2}. {tag:<10} {topic}")
        # Word-wrap desc
        words = desc.split()
        line = "        "
        for w in words:
            if len(line) + len(w) > 78:
                print(line)
                line = "        " + w + " "
            else:
                line += w + " "
        if line.strip():
            print(line)
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║    DOTA 2 POLYMARKET BOT — STRATEGY DEEP ANALYSIS                   ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")

    # Load all files
    sig  = load(SIGNALS_PATH)
    att  = load(LIVE_ATT_PATH)
    mko  = load(MARKOUTS_PATH)
    ev   = load(DOTA_EV_PATH)
    bk   = load(BOOK_EV_PATH)
    bmv  = load(BOOK_MV_PATH)
    lat  = load(LATENCY_PATH)
    src  = load(SOURCE_DELAY_PATH)
    shad = load(SHADOW_PATH)
    snap = load(SNAPSHOTS_PATH)

    section_inventory()
    section_signal_funnel(sig)
    section_edge_calibration(mko, sig)
    section_live_rejections(att)
    section_book_quality(bk, sig, bmv)
    section_event_quality(ev, sig)
    section_latency(lat, src, snap)
    section_threshold_sensitivity(mko, sig)
    section_strategy_report(ev, sig, mko, att)
    section_recommendations(sig, att, mko, bk, ev)

    print()
    print("═" * 72)
    print("  Analysis complete.")
    print("═" * 72)
    print()


if __name__ == "__main__":
    main()
