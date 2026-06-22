#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


FEATURE_COLUMNS = [
    "market_mid",
    "ask",
    "bid",
    "spread",
    "game_time_sec",
    "source_update_age_sec",
    "book_age_ms",
    "token_net_worth_lead",
    "token_score_margin",
    "token_net_worth_lead_per_min",
    "lead_delta_15s",
    "lead_delta_30s",
    "lead_delta_60s",
    "score_delta_30s",
    "lead_velocity_30s",
    "lead_accel_60s",
    "lead_volatility_60s",
    "mid_delta_15s",
    "mid_delta_30s",
    "mid_delta_60s",
    "ask_delta_30s",
    "spread_delta_30s",
]


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _side_from_row(row: pd.Series) -> str:
    token_id = str(row.get("token_id") or "")
    if token_id == str(row.get("yes_token_id") or ""):
        return "YES"
    if token_id == str(row.get("no_token_id") or ""):
        return "NO"
    return ""


def _future_mid_for_group(group: pd.DataFrame, seconds: int) -> pd.Series:
    timestamps = group["timestamp_ns"].to_numpy()
    mids = group["market_mid"].to_numpy()
    targets = timestamps + int(seconds * 1_000_000_000)
    idx = np.searchsorted(timestamps, targets, side="left")
    out = np.full(len(group), np.nan)
    valid = idx < len(group)
    out[valid] = mids[idx[valid]]
    return pd.Series(out, index=group.index)


def _lag_value(group: pd.DataFrame, column: str, seconds: int) -> pd.Series:
    timestamps = group["timestamp_ns"].to_numpy()
    values = group[column].to_numpy()
    targets = timestamps - int(seconds * 1_000_000_000)
    idx = np.searchsorted(timestamps, targets, side="right") - 1
    out = np.full(len(group), np.nan)
    valid = idx >= 0
    out[valid] = values[idx[valid]]
    return pd.Series(out, index=group.index)


def _grouped_series(df: pd.DataFrame, group_cols: list[str], func) -> pd.Series:
    parts = [func(group) for _, group in df.groupby(group_cols, sort=False)]
    if not parts:
        return pd.Series(dtype=float)
    return pd.concat(parts).sort_index()


def build_dataset(
    replay: pd.DataFrame,
    *,
    max_source_age_sec: float,
    max_book_age_ms: float,
    max_spread: float,
    max_ask: float,
    min_game_time_sec: float,
    max_game_time_sec: float,
) -> tuple[pd.DataFrame, dict]:
    df = replay.copy()

    required = [
        "timestamp_ns",
        "match_id",
        "token_id",
        "yes_token_id",
        "no_token_id",
        "best_bid",
        "best_ask",
        "book_received_at_ns",
        "data_source",
        "source_update_age_sec",
        "game_time_sec",
        "radiant_lead",
        "radiant_net_worth",
        "dire_net_worth",
        "radiant_score",
        "dire_score",
        "steam_side_mapping",
        "token_net_worth_lead",
        "token_score_margin",
        "settlement_outcome",
    ]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise RuntimeError(f"Replay is missing required v2 columns: {', '.join(missing)}")

    before_rows = len(df)
    before_matches = int(df["match_id"].astype(str).nunique())

    for col in [
        "timestamp_ns",
        "best_bid",
        "best_ask",
        "book_received_at_ns",
        "source_update_age_sec",
        "game_time_sec",
        "radiant_lead",
        "radiant_net_worth",
        "dire_net_worth",
        "radiant_score",
        "dire_score",
        "token_net_worth_lead",
        "token_score_margin",
    ]:
        df[col] = _num(df[col])

    df["match_id"] = df["match_id"].astype(str)
    df["token_id"] = df["token_id"].astype(str)
    df["side"] = df.apply(_side_from_row, axis=1)
    df["bid"] = df["best_bid"]
    df["ask"] = df["best_ask"]
    df["market_mid"] = (df["bid"] + df["ask"]) / 2.0
    df["spread"] = df["ask"] - df["bid"]
    df["book_age_ms"] = (df["timestamp_ns"] - df["book_received_at_ns"]) / 1_000_000.0
    df["settlement_binary"] = df["settlement_outcome"].map({"WIN": 1.0, "LOSS": 0.0, 1: 1.0, 0: 0.0, "1": 1.0, "0": 0.0})
    radiant_lead = df["radiant_lead"].where(
        df["radiant_lead"].notna(),
        df["radiant_net_worth"] - df["dire_net_worth"],
    )
    radiant_score_margin = df["radiant_score"] - df["dire_score"]
    yes_is_radiant = df["steam_side_mapping"].astype(str).eq("normal")
    token_is_yes = df["side"].eq("YES")
    token_is_radiant = (token_is_yes & yes_is_radiant) | (~token_is_yes & ~yes_is_radiant)
    derived_lead = np.where(token_is_radiant, radiant_lead, -radiant_lead)
    derived_score = np.where(token_is_radiant, radiant_score_margin, -radiant_score_margin)
    df["token_net_worth_lead"] = df["token_net_worth_lead"].where(df["token_net_worth_lead"].notna(), derived_lead)
    df["token_score_margin"] = df["token_score_margin"].where(df["token_score_margin"].notna(), derived_score)
    safe_minutes = np.maximum(df["game_time_sec"] / 60.0, 5.0)
    df["token_net_worth_lead_per_min"] = df["token_net_worth_lead"] / safe_minutes

    gate_masks = {
        "top_live": df["data_source"].eq("top_live"),
        "resolved": df["settlement_binary"].isin([0.0, 1.0]),
        "side_known": df["side"].isin(["YES", "NO"]),
        "source_fresh": df["source_update_age_sec"].between(0, max_source_age_sec, inclusive="both"),
        "book_age_valid": df["book_age_ms"].between(0, max_book_age_ms, inclusive="both"),
        "book_prices_valid": df["bid"].between(0, 1, inclusive="both") & df["ask"].between(0, 1, inclusive="both") & (df["ask"] >= df["bid"]),
        "spread_ok": df["spread"].between(0, max_spread, inclusive="both"),
        "ask_ok": df["ask"].between(0.05, max_ask, inclusive="both"),
        "game_time_ok": df["game_time_sec"].between(min_game_time_sec, max_game_time_sec, inclusive="both"),
        "features_present": df[["token_net_worth_lead", "token_score_margin"]].notna().all(axis=1),
    }
    gate_counts = {name: int(mask.sum()) for name, mask in gate_masks.items()}
    gate = pd.Series(True, index=df.index)
    for mask in gate_masks.values():
        gate &= mask

    out = df.loc[gate].copy()
    out = out.sort_values(["match_id", "token_id", "timestamp_ns"]).reset_index(drop=True)

    group_cols = ["match_id", "token_id"]
    for seconds in (15, 30, 60):
        out[f"lead_lag_{seconds}s"] = _grouped_series(out, group_cols, lambda g, s=seconds: _lag_value(g, "token_net_worth_lead", s))
        out[f"mid_lag_{seconds}s"] = _grouped_series(out, group_cols, lambda g, s=seconds: _lag_value(g, "market_mid", s))
    out["score_lag_30s"] = _grouped_series(out, group_cols, lambda g: _lag_value(g, "token_score_margin", 30))
    out["ask_lag_30s"] = _grouped_series(out, group_cols, lambda g: _lag_value(g, "ask", 30))
    out["spread_lag_30s"] = _grouped_series(out, group_cols, lambda g: _lag_value(g, "spread", 30))

    for seconds in (15, 30, 60):
        out[f"lead_delta_{seconds}s"] = out["token_net_worth_lead"] - out[f"lead_lag_{seconds}s"]
        out[f"mid_delta_{seconds}s"] = out["market_mid"] - out[f"mid_lag_{seconds}s"]
    out["score_delta_30s"] = out["token_score_margin"] - out["score_lag_30s"]
    out["ask_delta_30s"] = out["ask"] - out["ask_lag_30s"]
    out["spread_delta_30s"] = out["spread"] - out["spread_lag_30s"]
    out["lead_velocity_30s"] = out["lead_delta_30s"] / 30.0
    out["lead_accel_60s"] = (out["lead_delta_30s"] - (out["lead_lag_30s"] - out["lead_lag_60s"])) / 60.0

    def rolling_vol(group: pd.DataFrame) -> pd.Series:
        return group["token_net_worth_lead"].rolling(window=4, min_periods=2).std()

    out["lead_volatility_60s"] = _grouped_series(out, group_cols, rolling_vol)
    for seconds in (120, 300, 900):
        out[f"future_mid_{seconds}s"] = _grouped_series(out, group_cols, lambda g, s=seconds: _future_mid_for_group(g, s))
        out[f"clv_{seconds}s"] = out[f"future_mid_{seconds}s"] - out["ask"]

    out["settlement_ev"] = out["settlement_binary"] - out["ask"]
    for slip in (0.01, 0.02, 0.03, 0.04, 0.05, 0.08):
        out[f"net_ev_after_{int(slip * 100):02d}c"] = out["settlement_binary"] - (out["ask"] + slip)

    # Require real prior state for velocity features. This is deliberately stricter
    # than v1 because duplicate same-timestamp rows should not create persistence.
    out = out.dropna(subset=FEATURE_COLUMNS + ["clv_120s", "clv_300s", "settlement_binary"]).copy()
    out = out.reset_index(drop=True)

    summary = {
        "input_rows": before_rows,
        "input_matches": before_matches,
        "output_rows": int(len(out)),
        "output_matches": int(out["match_id"].nunique()) if not out.empty else 0,
        "gate_counts_individual": gate_counts,
        "gate_counts_all_before_velocity": int(gate.sum()),
        "settings": {
            "max_source_age_sec": max_source_age_sec,
            "max_book_age_ms": max_book_age_ms,
            "max_spread": max_spread,
            "max_ask": max_ask,
            "min_game_time_sec": min_game_time_sec,
            "max_game_time_sec": max_game_time_sec,
        },
        "feature_columns": FEATURE_COLUMNS,
        "label_columns": ["settlement_binary", "settlement_ev", "clv_120s", "clv_300s", "clv_900s", "net_ev_after_04c"],
        "notes": [
            "Features are restricted to gettoplive state and Polymarket book fields.",
            "Settlement and future mid columns are labels/evaluation fields only.",
        ],
    }
    return out, summary


def write_report(summary: dict, out: pd.DataFrame, path: Path) -> None:
    lines = ["# Model Value Dataset V2\n"]
    lines.append(f"- Input rows: {summary['input_rows']}")
    lines.append(f"- Input matches: {summary['input_matches']}")
    lines.append(f"- Output rows: {summary['output_rows']}")
    lines.append(f"- Output matches: {summary['output_matches']}")
    lines.append("")
    lines.append("## Settings")
    for key, value in summary["settings"].items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("## Individual Gate Counts")
    for key, value in summary["gate_counts_individual"].items():
        lines.append(f"- {key}: {value}")
    lines.append(f"- all gates before velocity requirement: {summary['gate_counts_all_before_velocity']}")
    if not out.empty:
        lines.append("")
        lines.append("## Dataset Diagnostics")
        lines.append(f"- Avg ask: {out['ask'].mean():.4f}")
        lines.append(f"- Avg spread: {out['spread'].mean():.4f}")
        lines.append(f"- Avg source age sec: {out['source_update_age_sec'].mean():.2f}")
        lines.append(f"- Avg book age ms: {out['book_age_ms'].mean():.1f}")
        lines.append(f"- Settlement win rate: {out['settlement_binary'].mean():.2%}")
        lines.append(f"- Avg CLV 120s: {out['clv_120s'].mean():.4f}")
        lines.append(f"- Avg CLV 300s: {out['clv_300s'].mean():.4f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-file", default="data_v2/model_value_replay.parquet")
    parser.add_argument("--out-file", default="data_v2/model_value_dataset_v2.parquet")
    parser.add_argument("--report-file", default="reports/model_value_dataset_v2_report.md")
    parser.add_argument("--summary-json", default="reports/model_value_dataset_v2_summary.json")
    parser.add_argument("--max-source-age-sec", type=float, default=15.0)
    parser.add_argument("--max-book-age-ms", type=float, default=2500.0)
    parser.add_argument("--max-spread", type=float, default=0.05)
    parser.add_argument("--max-ask", type=float, default=0.80)
    parser.add_argument("--min-game-time-sec", type=float, default=420.0)
    parser.add_argument("--max-game-time-sec", type=float, default=2400.0)
    args = parser.parse_args()

    replay_path = Path(args.replay_file)
    replay = pd.read_parquet(replay_path) if replay_path.suffix == ".parquet" else pd.read_csv(replay_path)
    out, summary = build_dataset(
        replay,
        max_source_age_sec=args.max_source_age_sec,
        max_book_age_ms=args.max_book_age_ms,
        max_spread=args.max_spread,
        max_ask=args.max_ask,
        min_game_time_sec=args.min_game_time_sec,
        max_game_time_sec=args.max_game_time_sec,
    )

    out_file = Path(args.out_file)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_file, index=False)

    report_file = Path(args.report_file)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    write_report(summary, out, report_file)

    summary_file = Path(args.summary_json)
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    summary_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
