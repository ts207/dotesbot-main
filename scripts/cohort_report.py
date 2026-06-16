#!/usr/bin/env python3
import os
from collections import Counter
from typing import Any

import pandas as pd

def load_csv(path):
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()

def bool_series(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(False, index=df.index)
    return df[col].astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})

def num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(dtype="float64")
    return pd.to_numeric(df[col], errors="coerce")

def mean_num(df: pd.DataFrame, col: str) -> float | None:
    s = num(df, col)
    if s.empty:
        return None
    out = s.mean()
    return None if pd.isna(out) else float(out)

def p50_num(df: pd.DataFrame, col: str) -> float | None:
    s = num(df, col).dropna()
    return None if s.empty else float(s.quantile(0.50))

def p90_num(df: pd.DataFrame, col: str) -> float | None:
    s = num(df, col).dropna()
    return None if s.empty else float(s.quantile(0.90))

def top_counts(df: pd.DataFrame, col: str, n: int = 5) -> list[tuple[Any, int]]:
    if df.empty or col not in df.columns:
        return []
    vals = df[col].fillna("").astype(str)
    vals = vals[vals != ""]
    return Counter(vals).most_common(n)

def execution_breakdown(df: pd.DataFrame) -> list[tuple[Any, int]]:
    return top_counts(df, "execution_path", 8)

def edge_buckets(df: pd.DataFrame) -> list[tuple[str, int]]:
    if df.empty or "edge" not in df.columns:
        return []
    buckets = pd.cut(
        num(df, "edge"),
        bins=[-10, 0, 0.05, 0.10, 0.15, 0.20, 0.30, 10],
        labels=["<=0", "0-5c", "5-10c", "10-15c", "15-20c", "20-30c", ">30c"],
    )
    return Counter(buckets.dropna().astype(str)).most_common()

def game_time_buckets(df: pd.DataFrame) -> list[tuple[str, int]]:
    if df.empty or "game_time_sec" not in df.columns:
        return []
    buckets = pd.cut(
        num(df, "game_time_sec") / 60.0,
        bins=[0, 10, 15, 20, 25, 30, 35, 200],
        labels=["0-10m", "10-15m", "15-20m", "20-25m", "25-30m", "30-35m", "35m+"],
    )
    return Counter(buckets.dropna().astype(str)).most_common()

def cohort_from_attempts(df: pd.DataFrame, *, fair_col: str = "fair_price") -> dict:
    if df.empty:
        return {}
    trade_mask = bool_series(df, "would_trade")
    sigs = df[trade_mask]
    rejs = df[~trade_mask]
    return {
        "candidates": len(df),
        "rejects": len(rejs),
        "signals": len(sigs),
        "reject_reasons": top_counts(rejs, "reject_reason"),
        "execution_path": execution_breakdown(df),
        "avg_ask": mean_num(sigs, "ask"),
        "avg_fair": mean_num(sigs, fair_col),
        "avg_edge": mean_num(sigs, "edge"),
        "avg_book_age_ms": mean_num(sigs, "book_age_ms"),
        "p50_book_age_ms": p50_num(sigs, "book_age_ms"),
        "p90_book_age_ms": p90_num(sigs, "book_age_ms"),
        "edge_buckets": edge_buckets(sigs),
        "game_time_buckets": game_time_buckets(sigs),
    }

def attach_trade_stats(cohort: dict, trades: pd.DataFrame) -> None:
    if trades.empty:
        return
    actions = trades.get("action", pd.Series("", index=trades.index)).astype(str).str.lower()
    entries = trades[actions == "entry"]
    exits = trades[actions == "exit"]
    cohort["entries"] = len(entries)
    cohort["exits"] = len(exits)
    cohort["trade_execution_path"] = execution_breakdown(trades)
    if len(entries) > 0:
        cohort["avg_entry_price"] = mean_num(entries, "entry_price")
        cohort["avg_entry_edge"] = mean_num(entries, "entry_edge")
    if len(exits) > 0:
        cohort["roi"] = mean_num(exits, "roi")
        pnl = num(exits, "pnl_usd").sum()
        cohort["pnl"] = None if pd.isna(pnl) else float(pnl)
        cohort["exit_reasons"] = top_counts(exits, "exit_reason")

def maybe_attach_markouts(cohort: dict, markouts: pd.DataFrame, token_ids: pd.Series | None) -> None:
    if markouts.empty or token_ids is None or token_ids.empty or "token_id" not in markouts.columns:
        return
    tids = {str(x) for x in token_ids.dropna().astype(str)}
    if not tids:
        return
    sub = markouts[markouts["token_id"].astype(str).isin(tids)]
    if sub.empty:
        return
    cohort["markout_3s"] = mean_num(sub, "markout_3s")
    cohort["markout_10s"] = mean_num(sub, "markout_10s")
    cohort["markout_30s"] = mean_num(sub, "markout_30s")

def allocator_attribution() -> None:
    """Print attribution stats from logs/strategy_allocator.csv.

    Answers: how often does EVENT preempt VALUE, and is the EVENT edge higher?
    """
    alloc = load_csv("logs/strategy_allocator.csv")
    if alloc.empty:
        print("\n=== STRATEGY ALLOCATOR ATTRIBUTION ===")
        print("  (no data — logs/strategy_allocator.csv not found or empty)")
        return

    import json as _json

    def _parse_json_list(s):
        try:
            return _json.loads(s)
        except Exception:
            return []

    total = len(alloc)
    already_entered = alloc[alloc.get("block_reason", pd.Series("", index=alloc.index)).fillna("") == "already_entered"] if "block_reason" in alloc.columns else pd.DataFrame()
    preempted = alloc[alloc.get("block_reason", pd.Series("", index=alloc.index)).fillna("").str.startswith("preempted")] if "block_reason" in alloc.columns else pd.DataFrame()

    print("\n=== STRATEGY ALLOCATOR ATTRIBUTION ===")
    print(f"  Total contested decisions: {total}")
    print(f"  Already-entered blocks:    {len(already_entered)}")
    print(f"  Preemption decisions:      {len(preempted)}")

    if not preempted.empty and "winner_strategy" in preempted.columns and "blocked_strategies" in preempted.columns:
        # Count (winner, blocked) pairs.
        pair_counts: Counter = Counter()
        for _, row in preempted.iterrows():
            winner = str(row.get("winner_strategy", ""))
            blocked_list = _parse_json_list(row.get("blocked_strategies", "[]"))
            for b in blocked_list:
                pair_counts[(winner, b)] += 1

        print("\n  Preemption pairs (winner → blocked):")
        for (w, b), cnt in pair_counts.most_common(10):
            print(f"    {w} → {b}: {cnt}x")

        # Edge comparison: EVENT preempting VALUE
        ev_preempts = preempted[
            preempted["winner_strategy"].fillna("").str.startswith("EVENT")
        ] if "winner_strategy" in preempted.columns else pd.DataFrame()

        if not ev_preempts.empty and "winner_edge" in ev_preempts.columns:
            winner_edges = pd.to_numeric(ev_preempts["winner_edge"], errors="coerce").dropna()
            # Extract VALUE edge from blocked_edges JSON
            value_blocked_edges = []
            for _, row in ev_preempts.iterrows():
                blocked_strats = _parse_json_list(row.get("blocked_strategies", "[]"))
                blocked_edge_vals = _parse_json_list(row.get("blocked_edges", "[]"))
                for strat, edge_val in zip(blocked_strats, blocked_edge_vals):
                    if strat == "VALUE_EDGE":
                        try:
                            value_blocked_edges.append(float(edge_val))
                        except (TypeError, ValueError):
                            pass

            print(f"\n  EVENT preempting VALUE ({len(ev_preempts)} decisions):")
            if not winner_edges.empty:
                print(f"    Avg EVENT edge:  {winner_edges.mean():.4f}")
            if value_blocked_edges:
                import statistics
                print(f"    Avg VALUE edge:  {statistics.mean(value_blocked_edges):.4f}  (n={len(value_blocked_edges)})")
                edge_diff = winner_edges.mean() - statistics.mean(value_blocked_edges) if not winner_edges.empty else None
                if edge_diff is not None:
                    direction = "better" if edge_diff > 0 else "worse"
                    print(f"    EVENT edge {direction} than VALUE by {abs(edge_diff):.4f}")

    if not already_entered.empty and "winner_strategy" in already_entered.columns:
        print("\n  Already-entered by strategy that was blocked:")
        for strat, cnt in top_counts(already_entered, "winner_strategy"):
            print(f"    {strat}: {cnt}x")


def generate_report():
    signals = load_csv("logs/strategy_signals.csv")
    value_att = load_csv("logs/value_attempts.csv")
    dswing_att = load_csv("logs/dswing_attempts.csv")
    paper_trades = load_csv("logs/paper_trades.csv")
    markouts = load_csv("logs/signal_markouts.csv")

    cohorts = {}

    if not value_att.empty:
        cohorts["VALUE_EDGE"] = cohort_from_attempts(value_att, fair_col="fair_price")
        maybe_attach_markouts(
            cohorts["VALUE_EDGE"],
            markouts,
            value_att.loc[bool_series(value_att, "would_trade"), "token_id"]
            if "token_id" in value_att.columns else None,
        )

    if not signals.empty:
        cont_mask = bool_series(signals, "is_continuation")
        rev_mask = bool_series(signals, "is_reversal")
        for name, sub in [
            ("ETV_CONTINUATION", signals[cont_mask]),
            ("ETV_REVERSAL", signals[rev_mask]),
        ]:
            trade_mask = bool_series(sub, "would_trade")
            s_sigs = sub[trade_mask]
            s_rejs = sub[~trade_mask]
            cohorts[name] = {
                "candidates": len(sub),
                "rejects": len(s_rejs),
                "signals": len(s_sigs),
                "reject_reasons": top_counts(s_rejs, "reject_reason"),
                "execution_path": execution_breakdown(sub),
                "actual_event_types": top_counts(sub, "actual_event_type"),
                "avg_ask": mean_num(s_sigs, "ask"),
                "avg_fair": mean_num(s_sigs, "fair_price"),
                "avg_edge": mean_num(s_sigs, "edge"),
                "avg_fair_delta": mean_num(s_sigs, "fair_delta"),
                "avg_book_age_ms": mean_num(s_sigs, "book_age_ms"),
                "p50_book_age_ms": p50_num(s_sigs, "book_age_ms"),
                "p90_book_age_ms": p90_num(s_sigs, "book_age_ms"),
                "edge_buckets": edge_buckets(s_sigs),
                "game_time_buckets": game_time_buckets(s_sigs),
            }
            maybe_attach_markouts(
                cohorts[name],
                markouts,
                s_sigs["token_id"] if "token_id" in s_sigs.columns else None,
            )

    if not dswing_att.empty:
        cohorts["DSWING"] = cohort_from_attempts(dswing_att, fair_col="series_fair")

    if not paper_trades.empty:
        sk = paper_trades.get("strategy_kind", pd.Series("", index=paper_trades.index)).fillna("").astype(str).str.upper()
        cont_trade = bool_series(paper_trades, "entry_is_continuation")
        rev_trade = bool_series(paper_trades, "entry_is_reversal")
        for cname in cohorts:
            if cname == "VALUE_EDGE":
                t_sub = paper_trades[sk.isin({"VALUE", "VALUE_EDGE"})]
            elif cname == "ETV_CONTINUATION":
                t_sub = paper_trades[(sk == "EVENT_CONTINUATION_EDGE") | cont_trade]
            elif cname == "ETV_REVERSAL":
                t_sub = paper_trades[(sk == "EVENT_REVERSAL_EDGE") | rev_trade]
            elif cname == "DSWING":
                t_sub = paper_trades[sk == "DSWING"]
            else:
                continue
            attach_trade_stats(cohorts[cname], t_sub)

    print("=== STRATEGY COHORT REPORT ===")
    for name, data in cohorts.items():
        print(f"\nCohort: {name}")
        print(f"  Candidates:      {data.get('candidates', 0)}")
        print(f"  Rejects:         {data.get('rejects', 0)} {data.get('reject_reasons', [])}")
        print(f"  Signals:         {data.get('signals', 0)}")
        print(f"  Execution Path:  {data.get('execution_path', [])}")
        if data.get("actual_event_types"):
            print(f"  Event Types:     {data.get('actual_event_types')}")
        for label, key in [
            ("Avg Ask", "avg_ask"),
            ("Avg Fair", "avg_fair"),
            ("Avg Edge", "avg_edge"),
            ("Avg Delta", "avg_fair_delta"),
            ("Avg Book Age", "avg_book_age_ms"),
            ("P50 Book Age", "p50_book_age_ms"),
            ("P90 Book Age", "p90_book_age_ms"),
            ("Markout 3s", "markout_3s"),
            ("Markout 10s", "markout_10s"),
            ("Markout 30s", "markout_30s"),
        ]:
            value = data.get(key)
            if value is not None:
                print(f"  {label:<14} {value:.4f}")
        print(f"  Edge Buckets:    {data.get('edge_buckets', [])}")
        print(f"  Time Buckets:    {data.get('game_time_buckets', [])}")

        if "entries" in data:
            print(f"  Entries:         {data.get('entries', 0)}")
            print(f"  Exits:           {data.get('exits', 0)}")
            print(f"  Trade Path:      {data.get('trade_execution_path', [])}")
            if data.get("roi") is not None:
                print(f"  ROI:             {data['roi']:.2%}")
            if data.get("pnl") is not None:
                print(f"  PnL:            ${data['pnl']:.2f}")
            print(f"  Exit Reasons:    {data.get('exit_reasons', [])}")

    allocator_attribution()

if __name__ == "__main__":
    generate_report()
