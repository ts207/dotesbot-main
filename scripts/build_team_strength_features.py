#!/usr/bin/env python3
"""Build no-leak rolling team-strength features for Model B v2."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


WINDOWS = [30, 90]
DIAGNOSTIC_WINDOWS = [180, 365]
SECONDS_PER_DAY = 86400


def normalize_id(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return ""
    if text.endswith(".0"):
        text = text[:-2]
    return text


def parse_ts_series(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    parsed = pd.to_datetime(series, utc=True, errors="coerce")
    numeric_mask = numeric.notna()
    if numeric_mask.any():
        parsed_numeric = pd.to_datetime(numeric[numeric_mask], unit="s", utc=True, errors="coerce")
        parsed.loc[numeric_mask] = parsed_numeric
    return parsed


def bool_value(value: Any) -> bool | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y"}:
        return True
    if text in {"0", "false", "f", "no", "n"}:
        return False
    return None


def load_history(paths: list[Path]) -> pd.DataFrame:
    frames = []
    for path in paths:
        if path.exists():
            frames.append(pd.read_csv(path))
    if not frames:
        return pd.DataFrame()

    history = pd.concat(frames, ignore_index=True, sort=False)
    if "match_id" in history.columns:
        history["match_id_norm"] = history["match_id"].map(normalize_id)
        history = history.drop_duplicates(subset=["match_id_norm"], keep="last")
    history["start_dt"] = parse_ts_series(history["start_ts"])
    for col in ["radiant_team_id", "dire_team_id", "winner_team_id"]:
        if col in history.columns:
            history[col] = history[col].map(normalize_id)
    if "winner_team_id" not in history.columns:
        history["winner_team_id"] = ""

    radiant_win = history["radiant_win"].map(bool_value) if "radiant_win" in history.columns else pd.Series([None] * len(history))
    missing_winner = history["winner_team_id"].eq("")
    history.loc[missing_winner & radiant_win.eq(True), "winner_team_id"] = history.loc[
        missing_winner & radiant_win.eq(True), "radiant_team_id"
    ]
    history.loc[missing_winner & radiant_win.eq(False), "winner_team_id"] = history.loc[
        missing_winner & radiant_win.eq(False), "dire_team_id"
    ]

    rows = []
    for _, row in history.iterrows():
        start_dt = row.get("start_dt")
        winner = normalize_id(row.get("winner_team_id"))
        radiant = normalize_id(row.get("radiant_team_id"))
        dire = normalize_id(row.get("dire_team_id"))
        match_id = normalize_id(row.get("match_id"))
        if pd.isna(start_dt) or not winner:
            continue
        for team_id in [radiant, dire]:
            if not team_id:
                continue
            rows.append(
                {
                    "team_id": team_id,
                    "start_dt": start_dt,
                    "match_id": match_id,
                    "won": 1.0 if team_id == winner else 0.0,
                }
            )
    team_history = pd.DataFrame(rows)
    if team_history.empty:
        return team_history
    return team_history.sort_values(["team_id", "start_dt"]).reset_index(drop=True)


def build_history_index(team_history: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {team_id: group.reset_index(drop=True) for team_id, group in team_history.groupby("team_id")}


def rolling_stats(index: dict[str, pd.DataFrame], team_id: str, start_dt: pd.Timestamp, days: int) -> dict[str, Any]:
    empty = {"count": 0, "winrate": np.nan, "max_history_ts": ""}
    if not team_id or pd.isna(start_dt) or team_id not in index:
        return empty
    group = index[team_id]
    window_start = start_dt - pd.Timedelta(days=days)
    hist = group[(group["start_dt"] < start_dt) & (group["start_dt"] >= window_start)]
    if hist.empty:
        return empty
    return {
        "count": int(len(hist)),
        "winrate": float(hist["won"].mean()),
        "max_history_ts": hist["start_dt"].max().isoformat(),
    }


def diff(a: Any, b: Any) -> float:
    if pd.isna(a) or pd.isna(b):
        return np.nan
    return float(a) - float(b)


def confidence(count_a: int, count_b: int) -> float:
    return float(min(1.0, min(count_a, count_b) / 10.0))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", default="data/train_pool/model_b_candidates.csv")
    parser.add_argument("--game-universe", default="data/processed/dota_game_universe.csv")
    parser.add_argument("--processed-games", default="data/processed/dota_games.csv")
    parser.add_argument("--output", default="data/features/team_strength_features.csv")
    parser.add_argument("--report", default="reports/team_strength_features_report.json")
    args = parser.parse_args()

    candidates = pd.read_csv(args.candidates)
    candidates["start_dt"] = parse_ts_series(candidates["start_ts"])
    for col in ["team_a_id", "team_b_id", "market_id", "game_id", "match_id"]:
        if col in candidates.columns:
            candidates[col] = candidates[col].map(normalize_id)

    history = load_history([Path(args.game_universe), Path(args.processed_games)])
    history_index = build_history_index(history)

    rows = []
    for _, row in candidates.iterrows():
        start_dt = row["start_dt"]
        team_a = normalize_id(row.get("team_a_id"))
        team_b = normalize_id(row.get("team_b_id"))
        out = {
            "market_id": normalize_id(row.get("market_id")),
            "condition_id": normalize_id(row.get("condition_id")),
            "event_id": normalize_id(row.get("event_id")),
            "yes_token_id": normalize_id(row.get("yes_token_id")),
            "no_token_id": normalize_id(row.get("no_token_id")),
            "slug": row.get("slug", ""),
            "question": row.get("question", ""),
            "source_universe": row.get("source_universe", ""),
            "game_id": normalize_id(row.get("game_id")),
            "match_id": normalize_id(row.get("match_id")),
            "series_id": normalize_id(row.get("series_id")),
            "start_ts": row.get("start_ts", ""),
            "feature_snapshot_ts": (start_dt - pd.Timedelta(seconds=1)).isoformat() if not pd.isna(start_dt) else "",
            "team_a_id": team_a,
            "team_b_id": team_b,
        }
        max_history_ts = []
        for days in WINDOWS + DIAGNOSTIC_WINDOWS:
            a_stats = rolling_stats(history_index, team_a, start_dt, days)
            b_stats = rolling_stats(history_index, team_b, start_dt, days)
            out[f"team_a_match_count_{days}d"] = a_stats["count"]
            out[f"team_b_match_count_{days}d"] = b_stats["count"]
            out[f"team_a_rolling_winrate_{days}d"] = a_stats["winrate"]
            out[f"team_b_rolling_winrate_{days}d"] = b_stats["winrate"]
            out[f"team_winrate_{days}d_diff"] = diff(a_stats["winrate"], b_stats["winrate"])
            if a_stats["max_history_ts"]:
                max_history_ts.append(a_stats["max_history_ts"])
            if b_stats["max_history_ts"]:
                max_history_ts.append(b_stats["max_history_ts"])
        out["team_strength_diff"] = out["team_winrate_90d_diff"]
        out["team_recent_form_diff"] = out["team_winrate_30d_diff"]
        out["team_match_count_90d_diff"] = int(out["team_a_match_count_90d"]) - int(out["team_b_match_count_90d"])
        out["team_a_strength_confidence"] = float(min(1.0, int(out["team_a_match_count_90d"]) / 10.0))
        out["team_b_strength_confidence"] = float(min(1.0, int(out["team_b_match_count_90d"]) / 10.0))
        out["team_strength_feature_confidence"] = confidence(
            int(out["team_a_match_count_90d"]), int(out["team_b_match_count_90d"])
        )
        out["max_history_ts"] = max(max_history_ts) if max_history_ts else ""
        out["no_leak_valid"] = bool(not out["max_history_ts"] or pd.Timestamp(out["max_history_ts"]) < start_dt)
        missing = []
        if not team_a:
            missing.append("missing_team_a_id")
        if not team_b:
            missing.append("missing_team_b_id")
        if int(out["team_a_match_count_90d"]) == 0:
            missing.append("no_team_a_90d_history")
        if int(out["team_b_match_count_90d"]) == 0:
            missing.append("no_team_b_90d_history")
        out["team_strength_missing_reason"] = ";".join(missing)
        rows.append(out)

    features = pd.DataFrame(rows)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    features.to_csv(output, index=False)

    coverage = {
        "rows": int(len(features)),
        "history_team_rows": int(len(history)),
        "unique_history_teams": int(history["team_id"].nunique()) if not history.empty else 0,
        "no_leak_violations": int((~features["no_leak_valid"]).sum()),
        "team_strength_diff_non_null": int(features["team_strength_diff"].notna().sum()),
        "team_recent_form_diff_non_null": int(features["team_recent_form_diff"].notna().sum()),
        "team_winrate_180d_diff_non_null": int(features["team_winrate_180d_diff"].notna().sum()),
        "team_winrate_365d_diff_non_null": int(features["team_winrate_365d_diff"].notna().sum()),
        "mean_team_strength_feature_confidence": float(features["team_strength_feature_confidence"].mean()) if len(features) else None,
        "rows_confidence_ge_0_5": int((features["team_strength_feature_confidence"] >= 0.5).sum()),
        "rows_confidence_ge_0_8": int((features["team_strength_feature_confidence"] >= 0.8).sum()),
        "candidate_start_min": candidates["start_dt"].min().isoformat() if candidates["start_dt"].notna().any() else None,
        "candidate_start_max": candidates["start_dt"].max().isoformat() if candidates["start_dt"].notna().any() else None,
        "history_start_min": history["start_dt"].min().isoformat() if not history.empty else None,
        "history_start_max": history["start_dt"].max().isoformat() if not history.empty else None,
        "candidate_team_ids_with_history_index": int(
            len(
                {
                    team_id
                    for team_id in pd.concat([candidates["team_a_id"], candidates["team_b_id"]]).map(normalize_id)
                    if team_id and team_id in history_index
                }
            )
        ),
        "candidate_unique_team_ids": int(
            len(
                {
                    team_id
                    for team_id in pd.concat([candidates["team_a_id"], candidates["team_b_id"]]).map(normalize_id)
                    if team_id
                }
            )
        ),
        "missing_reason_counts": {
            str(k): int(v)
            for k, v in features["team_strength_missing_reason"].replace("", "none").value_counts().to_dict().items()
        },
    }
    report = {
        "status": "ok" if coverage["no_leak_violations"] == 0 else "fail_no_leak_violation",
        "feature_group": "team_strength",
        "windows_days": WINDOWS,
        "diagnostic_windows_days": DIAGNOSTIC_WINDOWS,
        "inputs": {
            "candidates": args.candidates,
            "game_universe": args.game_universe,
            "processed_games": args.processed_games,
        },
        "output": args.output,
        **coverage,
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
