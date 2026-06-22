#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = [
    "timestamp_ns",
    "match_id",
    "token_id",
    "side",
    "ask",
    "bid",
    "market_mid",
    "spread",
    "source_update_age_sec",
    "book_age_ms",
    "game_time_sec",
    "token_net_worth_lead",
    "token_score_margin",
    "lead_delta_30s",
    "lead_delta_60s",
    "score_delta_30s",
    "mid_delta_30s",
    "mid_delta_60s",
    "ask_delta_30s",
    "clv_120s",
    "clv_300s",
    "settlement_binary",
]


def _num(df: pd.DataFrame, columns: list[str]) -> None:
    for col in columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")


def build_reaction_lag_dataset(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise RuntimeError(f"Input dataset missing required columns: {', '.join(missing)}")

    out = df.copy()
    out["match_id"] = out["match_id"].astype(str)
    out["token_id"] = out["token_id"].astype(str)
    numeric_columns = [
        "timestamp_ns",
        "ask",
        "bid",
        "market_mid",
        "spread",
        "source_update_age_sec",
        "book_age_ms",
        "game_time_sec",
        "token_net_worth_lead",
        "token_score_margin",
        "lead_delta_30s",
        "lead_delta_60s",
        "score_delta_30s",
        "mid_delta_30s",
        "mid_delta_60s",
        "ask_delta_30s",
        "clv_120s",
        "clv_300s",
        "settlement_binary",
    ]
    if "clv_900s" in out.columns:
        numeric_columns.append("clv_900s")
    _num(out, numeric_columns)

    # Price units are probability points. State coefficients are intentionally
    # simple and monotonic so the sweep tests a thesis, not a fitted model.
    out["state_price_equiv_30s"] = (
        0.00004 * out["lead_delta_30s"]
        + 0.02000 * out["score_delta_30s"]
    )
    out["state_price_equiv_60s"] = (
        0.00003 * out["lead_delta_60s"]
        + 0.01500 * out["score_delta_30s"]
    )
    out["state_move_score"] = 0.65 * out["state_price_equiv_30s"] + 0.35 * out["state_price_equiv_60s"]
    out["price_response_score"] = 0.75 * out["mid_delta_30s"] + 0.25 * out["mid_delta_60s"]
    out["reaction_lag_score"] = out["state_move_score"] - out["price_response_score"]
    out["abs_state_move_score"] = out["state_move_score"].abs()
    out["state_price_response_ratio"] = np.where(
        out["state_move_score"].abs() >= 0.01,
        out["price_response_score"] / out["state_move_score"],
        np.nan,
    )
    out["favorable_state_move"] = out["state_move_score"] > 0
    out["wrong_way_or_flat_price"] = out["price_response_score"] <= 0.0
    out["underreacted_price"] = out["reaction_lag_score"] > 0

    for cents in (0, 1, 2, 3, 4, 5, 8):
        slip = cents / 100.0
        out[f"clv_120s_after_{cents:02d}c"] = out["clv_120s"] - slip
        out[f"clv_300s_after_{cents:02d}c"] = out["clv_300s"] - slip
        if "clv_900s" in out.columns:
            out[f"clv_900s_after_{cents:02d}c"] = out["clv_900s"] - slip
        fill = out["ask"] + slip
        out[f"exit_roi_120s_{cents:02d}c"] = np.where(fill > 0, (out["ask"] + out["clv_120s"]) / fill - 1.0, np.nan)
        out[f"exit_roi_300s_{cents:02d}c"] = np.where(fill > 0, (out["ask"] + out["clv_300s"]) / fill - 1.0, np.nan)

    out = out.dropna(
        subset=[
            "state_move_score",
            "price_response_score",
            "reaction_lag_score",
            "clv_120s",
            "clv_300s",
            "settlement_binary",
        ]
    ).copy()
    out = out.sort_values(["timestamp_ns", "match_id", "token_id"]).reset_index(drop=True)

    candidate = out[
        out["favorable_state_move"]
        & out["underreacted_price"]
        & (out["reaction_lag_score"] >= 0.02)
    ]
    summary = {
        "input_rows": int(len(df)),
        "input_matches": int(df["match_id"].astype(str).nunique()) if "match_id" in df.columns else 0,
        "output_rows": int(len(out)),
        "output_matches": int(out["match_id"].nunique()) if not out.empty else 0,
        "candidate_rows_reaction_lag_02": int(len(candidate)),
        "candidate_matches_reaction_lag_02": int(candidate["match_id"].nunique()) if not candidate.empty else 0,
        "feature_columns": [
            "state_price_equiv_30s",
            "state_price_equiv_60s",
            "state_move_score",
            "price_response_score",
            "reaction_lag_score",
            "state_price_response_ratio",
            "wrong_way_or_flat_price",
        ],
        "notes": [
            "Input is expected to be the strict gettoplive/live-parity dataset.",
            "Features use only current and lagged gettoplive state plus current and lagged Polymarket book fields.",
            "CLV and settlement columns are labels/evaluation fields only.",
        ],
    }
    if not out.empty:
        summary["diagnostics"] = {
            "avg_ask": float(out["ask"].mean()),
            "avg_state_move_score": float(out["state_move_score"].mean()),
            "avg_price_response_score": float(out["price_response_score"].mean()),
            "avg_reaction_lag_score": float(out["reaction_lag_score"].mean()),
            "avg_clv_120s": float(out["clv_120s"].mean()),
            "avg_clv_300s": float(out["clv_300s"].mean()),
        }
    return out, summary


def write_report(summary: dict, out: pd.DataFrame, path: Path) -> None:
    lines = ["# Reaction Lag Dataset V1\n"]
    lines.append(f"- Input rows: {summary['input_rows']}")
    lines.append(f"- Input matches: {summary['input_matches']}")
    lines.append(f"- Output rows: {summary['output_rows']}")
    lines.append(f"- Output matches: {summary['output_matches']}")
    lines.append(f"- Candidate rows, lag >= 0.02: {summary['candidate_rows_reaction_lag_02']}")
    lines.append(f"- Candidate matches, lag >= 0.02: {summary['candidate_matches_reaction_lag_02']}")
    lines.append("")
    lines.append("## Feature Columns")
    for col in summary["feature_columns"]:
        lines.append(f"- {col}")
    if summary.get("diagnostics"):
        lines.append("")
        lines.append("## Diagnostics")
        for key, value in summary["diagnostics"].items():
            lines.append(f"- {key}: {value:.6f}")
    if not out.empty:
        lines.append("")
        lines.append("## Candidate Buckets")
        lines.append("| lag_min | rows | matches | avg_clv_120s | avg_clv_300s |")
        lines.append("|---:|---:|---:|---:|---:|")
        for threshold in (0.02, 0.04, 0.06, 0.08, 0.10):
            group = out[
                out["favorable_state_move"]
                & out["underreacted_price"]
                & (out["reaction_lag_score"] >= threshold)
            ]
            if group.empty:
                lines.append(f"| {threshold:.2f} | 0 | 0 | n/a | n/a |")
            else:
                lines.append(
                    f"| {threshold:.2f} | {len(group)} | {group['match_id'].nunique()} | "
                    f"{group['clv_120s'].mean():.6f} | {group['clv_300s'].mean():.6f} |"
                )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-file", default="data_v2/model_value_dataset_v2.parquet")
    parser.add_argument("--out-file", default="data_v2/reaction_lag_dataset_v1.parquet")
    parser.add_argument("--report-file", default="reports/reaction_lag_dataset_v1_report.md")
    parser.add_argument("--summary-json", default="reports/reaction_lag_dataset_v1_summary.json")
    args = parser.parse_args()

    input_path = Path(args.input_file)
    df = pd.read_parquet(input_path) if input_path.suffix == ".parquet" else pd.read_csv(input_path)
    out, summary = build_reaction_lag_dataset(df)

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
