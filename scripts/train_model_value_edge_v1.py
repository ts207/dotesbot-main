#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import lightgbm as lgb
except ImportError as exc:
    raise SystemExit(
        "Missing lightgbm. Install with: pip install lightgbm"
    ) from exc

from sklearn.metrics import mean_squared_error, mean_absolute_error, roc_auc_score, brier_score_loss


FEATURES = [
    "market_mid",
    "ask",
    "spread",
    "game_time_sec",
    "token_net_worth_lead",
    "token_score_margin",
    "token_net_worth_lead_per_min",
]


def _first_present(df: pd.DataFrame, names: list[str]) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None


def normalize_replay(df: pd.DataFrame) -> pd.DataFrame:
    """
    Expects one row per token-side snapshot OR enough columns to derive one.

    Required final columns:
      timestamp_ns
      match_id
      token_id
      best_bid
      best_ask
      game_time_sec
      token_net_worth_lead
      token_score_margin
      settlement_outcome
    """

    out = df.copy()

    aliases = {
        "timestamp_ns": ["timestamp_ns", "ts_ns", "received_at_ns", "snapshot_received_at_ns"],
        "match_id": ["match_id", "dota_match_id"],
        "token_id": ["token_id", "yes_token_id"],  # Let's map yes_token_id to token_id if needed, but it requires unpivoting
        "best_bid": ["best_bid", "bid", "exec_best_bid"],
        "best_ask": ["best_ask", "ask", "exec_best_ask"],
        "game_time_sec": ["game_time_sec", "game_time"],
        "settlement_outcome": [
            "settlement_outcome",
            "token_won",
            "official_win",
            "y",
            "outcome",
        ],
        "token_net_worth_lead": [
            "token_net_worth_lead",
            "token_networth_lead",
            "net_worth_lead",
        ],
        "token_score_margin": [
            "token_score_margin",
            "score_margin",
            "token_kill_margin",
        ],
    }

    # Custom alias for generated_large_replay_data.csv (we have yes_token_id, no_token_id, yes_best_ask, etc.)
    # We need to unpivot it to token level
    if "yes_token_id" in out.columns and "token_id" not in out.columns:
        # It's our custom pivoted format, need to melt
        yes_df = out.copy()
        yes_df["token_id"] = yes_df["yes_token_id"]
        yes_df["best_bid"] = yes_df["yes_best_bid"]
        yes_df["best_ask"] = yes_df["yes_best_ask"]
        yes_df["settlement_outcome"] = yes_df["settlement_yes_outcome"] if "settlement_yes_outcome" in yes_df.columns else yes_df.get("settled_yes_outcome", np.nan)
        # For yes, radiant_net_worth - dire_net_worth if it's normal mapping, this is a bit tricky
        # In our script steam_side_mapping is normal, and it's always YES=radiant for now
        yes_df["token_net_worth_lead"] = yes_df.get("radiant_net_worth", 0) - yes_df.get("dire_net_worth", 0)
        yes_df["token_score_margin"] = yes_df.get("radiant_score", 0) - yes_df.get("dire_score", 0)

        no_df = out.copy()
        no_df["token_id"] = no_df["no_token_id"]
        no_df["best_bid"] = no_df["no_best_bid"]
        no_df["best_ask"] = no_df["no_best_ask"]
        no_df["settlement_outcome"] = no_df["settlement_no_outcome"] if "settlement_no_outcome" in no_df.columns else no_df.get("settled_no_outcome", np.nan)
        no_df["token_net_worth_lead"] = no_df.get("dire_net_worth", 0) - no_df.get("radiant_net_worth", 0)
        no_df["token_score_margin"] = no_df.get("dire_score", 0) - no_df.get("radiant_score", 0)
        
        out = pd.concat([yes_df, no_df], ignore_index=True)

    rename = {}
    for canonical, candidates in aliases.items():
        present = _first_present(out, candidates)
        if present is not None and present != canonical:
            rename[present] = canonical

    out = out.rename(columns=rename)

    required = [
        "timestamp_ns",
        "match_id",
        "token_id",
        "best_bid",
        "best_ask",
        "game_time_sec",
        "token_score_margin",
        "settlement_outcome",
    ]

    missing = [c for c in required if c not in out.columns]
    if missing:
        raise RuntimeError(
            "Replay data is missing required columns after alias normalization: "
            + ", ".join(missing)
            + "\nAvailable columns: "
            + ", ".join(out.columns)
        )

    for c in [
        "best_bid",
        "best_ask",
        "game_time_sec",
        "token_net_worth_lead",
        "token_score_margin",
    ]:
        out[c] = pd.to_numeric(out[c], errors="coerce")

    # Map WIN/LOSS to 1/0
    out["settlement_outcome"] = out["settlement_outcome"].map({"WIN": 1, "LOSS": 0, 1: 1, 0: 0, "1": 1, "0": 0})

    out = out.dropna(subset=required)

    out["market_mid"] = (out["best_bid"] + out["best_ask"]) / 2.0
    out["ask"] = out["best_ask"]
    out["spread"] = out["best_ask"] - out["best_bid"]

    # Runtime feature builder protects against zero time similarly.
    safe_minutes = np.maximum(out["game_time_sec"].astype(float) / 60.0, 5.0)
    out["token_net_worth_lead_per_min"] = out["token_net_worth_lead"] / safe_minutes

    # Basic book sanity & early game filter (noise reduction)
    out = out[
        (out["best_bid"] >= 0.0)
        & (out["best_ask"] <= 1.0)
        & (out["best_ask"] >= out["best_bid"])
        & (out["market_mid"] > 0.0)
        & (out["market_mid"] < 1.0)
        & (out["settlement_outcome"].isin([0, 1]))
        & (out["game_time_sec"] >= 420.0)
    ].copy()

    # Target for residual model.
    out["target_residual"] = out["settlement_outcome"] - out["market_mid"]

    # Optional clipping. Prevents a few extreme rows from making absurd residual predictions.
    out["target_residual"] = out["target_residual"].clip(-0.35, 0.35)

    return out


def chronological_match_split(df: pd.DataFrame, train_frac: float = 0.8):
    match_times = (
        df.groupby("match_id")["timestamp_ns"]
        .min()
        .sort_values()
    )
    n_train = int(len(match_times) * train_frac)
    train_matches = set(match_times.iloc[:n_train].index)
    valid_matches = set(match_times.iloc[n_train:].index)

    train = df[df["match_id"].isin(train_matches)].copy()
    valid = df[df["match_id"].isin(valid_matches)].copy()

    return train, valid


def train_model(train: pd.DataFrame, valid: pd.DataFrame) -> lgb.Booster:
    params = {
        "objective": "regression",
        "metric": "l2",
        "learning_rate": 0.03,
        "num_leaves": 15,
        "max_depth": 4,
        "min_data_in_leaf": 80,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.9,
        "bagging_freq": 1,
        "lambda_l2": 2.0,
        "verbosity": -1,

        # Critical: current runtime predictor only sums tree leaf values.
        # Disable boost_from_average so there is no hidden init score missing from export.
        "boost_from_average": False,
        "seed": 1811,
    }

    dtrain = lgb.Dataset(train[FEATURES], label=train["target_residual"], feature_name=FEATURES)
    dvalid = lgb.Dataset(valid[FEATURES], label=valid["target_residual"], feature_name=FEATURES)

    booster = lgb.train(
        params=params,
        train_set=dtrain,
        num_boost_round=300,
        valid_sets=[dtrain, dvalid],
        valid_names=["train", "valid"],
        callbacks=[
            lgb.early_stopping(stopping_rounds=40),
            lgb.log_evaluation(period=25),
        ],
    )

    return booster


def evaluate_model(booster: lgb.Booster, df: pd.DataFrame, name: str) -> dict:
    pred_residual = booster.predict(df[FEATURES], num_iteration=booster.best_iteration)
    p_model = np.clip(df["market_mid"].to_numpy() + pred_residual, 0.0, 1.0)

    y = df["settlement_outcome"].to_numpy()
    market_mid = df["market_mid"].to_numpy()

    out = {
        "split": name,
        "rows": int(len(df)),
        "matches": int(df["match_id"].nunique()),
        "residual_rmse": float(mean_squared_error(df["target_residual"], pred_residual) ** 0.5),
        "residual_mae": float(mean_absolute_error(df["target_residual"], pred_residual)),
        "brier_model": float(brier_score_loss(y, p_model)),
        "brier_market_mid": float(brier_score_loss(y, market_mid)),
        "pred_residual_min": float(np.min(pred_residual)),
        "pred_residual_p01": float(np.quantile(pred_residual, 0.01)),
        "pred_residual_p50": float(np.quantile(pred_residual, 0.50)),
        "pred_residual_p99": float(np.quantile(pred_residual, 0.99)),
        "pred_residual_max": float(np.max(pred_residual)),
    }

    try:
        out["auc_model"] = float(roc_auc_score(y, p_model))
        out["auc_market_mid"] = float(roc_auc_score(y, market_mid))
    except ValueError:
        out["auc_model"] = None
        out["auc_market_mid"] = None

    return out


def threshold_sweep(booster: lgb.Booster, valid: pd.DataFrame) -> list[dict]:
    pred_residual = booster.predict(valid[FEATURES], num_iteration=booster.best_iteration)
    p_model = np.clip(valid["market_mid"].to_numpy() + pred_residual, 0.0, 1.0)

    tmp = valid.copy()
    tmp["p_model"] = p_model
    tmp["edge"] = tmp["p_model"] - tmp["ask"]

    rows = []
    for threshold in [0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05, 0.075, 0.10]:
        sig = tmp[
            (tmp["edge"] >= threshold)
            & (tmp["spread"] <= 0.05)
            & (tmp["ask"].between(0.05, 0.95))
        ].copy()

        # Approximate one trade per match, earliest qualifying signal.
        if not sig.empty:
            sig = sig.sort_values("timestamp_ns").drop_duplicates("match_id", keep="first")

        if sig.empty:
            rows.append({
                "threshold": threshold,
                "trades": 0,
                "win_rate": None,
                "avg_ask": None,
                "one_share_profit": 0.0,
                "one_share_roi": None,
            })
            continue

        profit = sig["settlement_outcome"] - sig["ask"]
        cost = sig["ask"]

        rows.append({
            "threshold": threshold,
            "trades": int(len(sig)),
            "win_rate": float(sig["settlement_outcome"].mean()),
            "avg_ask": float(sig["ask"].mean()),
            "avg_edge": float(sig["edge"].mean()),
            "one_share_profit": float(profit.sum()),
            "one_share_roi": float(profit.sum() / cost.sum()),
        })

    return rows


def export_artifact(
    booster: lgb.Booster,
    out_dir: Path,
    train: pd.DataFrame,
    valid: pd.DataFrame,
    metrics: dict,
    sweep: list[dict],
):
    out_dir.mkdir(parents=True, exist_ok=True)

    dumped = booster.dump_model(num_iteration=booster.best_iteration)

    # Keep only the structure the runtime predictor actually needs.
    model_json = {
        "name": "model_value_edge_v1_residual_lgbm",
        "tree_info": dumped["tree_info"],
    }

    (out_dir / "model.json").write_text(json.dumps(model_json, indent=2), encoding="utf-8")
    (out_dir / "features.json").write_text(json.dumps(FEATURES, indent=2), encoding="utf-8")

    used_features = sorted(
        {
            FEATURES[node["split_feature"]]
            for tree in dumped["tree_info"]
            for node in walk_tree(tree["tree_structure"])
            if "split_feature" in node
        }
    )

    metadata = {
        "version": "model_value_edge_v1_residual_lgbm",
        "model_name": "model_value_edge_v1_residual_lgbm",
        "model_class": "lightgbm_residual_regressor",
        "strategy": "MODEL_VALUE_EDGE",
        "residual_mode": True,
        "uses_market_price": True,
        "uses_game_time": True,
        "uses_objectives": False,
        "uses_orderbook_dynamics": True,
        "deployment_status": "paper_only",
        "target": "settlement_outcome - market_mid",
        "features": FEATURES,
        "features_used_by_trees": used_features,
        "num_trees": len(model_json["tree_info"]),
        "best_iteration": int(booster.best_iteration or len(model_json["tree_info"])),
        "train_rows": int(len(train)),
        "train_matches": int(train["match_id"].nunique()),
        "valid_rows": int(len(valid)),
        "valid_matches": int(valid["match_id"].nunique()),
        "metrics": metrics,
        "threshold_sweep_valid": sweep,
        "notes": [
            "Runtime prediction is p_model = clamp(market_mid + tree_sum, 0, 1).",
            "Backtest must use residual-scale thresholds, not old 0.15 direct-probability threshold.",
            "No real-live deployment until live-parity paper validation passes.",
        ],
    }

    (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def walk_tree(node: dict):
    yield node
    if "left_child" in node:
        yield from walk_tree(node["left_child"])
    if "right_child" in node:
        yield from walk_tree(node["right_child"])


def chronological_kfold_cv(df: pd.DataFrame, n_splits: int = 5):
    match_times = df.groupby("match_id")["timestamp_ns"].min().sort_values()
    matches = list(match_times.index)
    fold_size = len(matches) // n_splits
    
    cv_metrics = []
    
    for i in range(n_splits):
        if i == n_splits - 1:
            valid_matches = set(matches[i * fold_size:])
            train_matches = set(matches[:i * fold_size])
        else:
            valid_matches = set(matches[i * fold_size:(i + 1) * fold_size])
            train_matches = set(matches[:i * fold_size] + matches[(i + 1) * fold_size:])
            
        train = df[df["match_id"].isin(train_matches)].copy()
        valid = df[df["match_id"].isin(valid_matches)].copy()
        
        if train.empty or valid.empty:
            continue
            
        booster = train_model(train, valid)
        v_met = evaluate_model(booster, valid, "valid")
        v_met["fold"] = i + 1
        cv_metrics.append(v_met)
        
    return cv_metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Unified token-side replay parquet/csv")
    parser.add_argument("--out-dir", default="models/dota_lgbm_win")
    parser.add_argument("--report-dir", default="reports/model_value_v1_training")
    args = parser.parse_args()

    input_path = Path(args.input)
    if input_path.suffix.lower() == ".parquet":
        raw = pd.read_parquet(input_path)
    else:
        raw = pd.read_csv(input_path)

    df = normalize_replay(raw)

    if df.empty:
        raise RuntimeError("No usable rows after normalization.")

    # 5-fold CV for robust metrics
    print("Running 5-fold cross-validation...")
    cv_metrics = chronological_kfold_cv(df, n_splits=5)
    for m in cv_metrics:
        print(f"Fold {m['fold']}: AUC={m.get('auc_model', 0):.4f}, Brier={m.get('brier_model', 0):.4f}")

    train, valid = chronological_match_split(df, train_frac=0.8)

    if train.empty or valid.empty:
        raise RuntimeError("Train/valid split produced empty data. Need multiple matches.")

    booster = train_model(train, valid)

    train_metrics = evaluate_model(booster, train, "train")
    valid_metrics = evaluate_model(booster, valid, "valid")
    sweep = threshold_sweep(booster, valid)

    metrics = {
        "train": train_metrics,
        "valid": valid_metrics,
        "cv_folds": cv_metrics,
    }

    out_dir = Path(args.out_dir)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    export_artifact(booster, out_dir, train, valid, metrics, sweep)

    pd.DataFrame([train_metrics, valid_metrics]).to_csv(report_dir / "metrics.csv", index=False)
    pd.DataFrame(sweep).to_csv(report_dir / "threshold_sweep_valid.csv", index=False)

    print("Wrote artifact to:", out_dir)
    print("Wrote report to:", report_dir)
    print(json.dumps(metrics, indent=2))
    print(pd.DataFrame(sweep).to_string(index=False))


if __name__ == "__main__":
    main()
