#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import brier_score_loss, mean_absolute_error, mean_squared_error, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from scripts.build_model_value_dataset_v2 import FEATURE_COLUMNS


SLIPPAGE_CENTS = [0, 1, 2, 3, 4, 5, 8]


def chronological_folds(df: pd.DataFrame, *, min_train_matches: int, folds: int) -> list[tuple[list[str], list[str]]]:
    match_order = df.groupby("match_id")["timestamp_ns"].min().sort_values().index.astype(str).tolist()
    remaining = len(match_order) - min_train_matches
    if remaining <= 0:
        return []
    fold_size = max(1, int(np.ceil(remaining / folds)))
    out: list[tuple[list[str], list[str]]] = []
    start = min_train_matches
    while start < len(match_order):
        end = min(len(match_order), start + fold_size)
        train_matches = match_order[:start]
        valid_matches = match_order[start:end]
        out.append((train_matches, valid_matches))
        start = end
    return out


def fit_model(train: pd.DataFrame, features: list[str], model_type: str):
    y = train["settlement_binary"] - train["market_mid"]
    if model_type == "ridge":
        model = make_pipeline(StandardScaler(), Ridge(alpha=5.0))
    else:
        model = HistGradientBoostingRegressor(
            learning_rate=0.04,
            max_iter=150,
            max_leaf_nodes=15,
            min_samples_leaf=30,
            l2_regularization=1.0,
            random_state=1811,
        )
    model.fit(train[features], y)
    return model


def add_predictions(df: pd.DataFrame, model, features: list[str], shrink: float) -> pd.DataFrame:
    out = df.copy()
    residual = np.asarray(model.predict(out[features]), dtype=float) * shrink
    out["pred_residual"] = residual
    out["p_model"] = np.clip(out["market_mid"] + residual, 0.0, 1.0)
    out["model_edge"] = out["p_model"] - out["ask"]
    return out


def first_trades(
    scored: pd.DataFrame,
    *,
    edge_threshold: float,
    slippage_for_gate: float,
    require_positive_clv: bool,
) -> pd.DataFrame:
    sig = scored[
        (scored["model_edge"] >= edge_threshold)
        & ((scored["p_model"] - (scored["ask"] + slippage_for_gate)) > 0)
    ].copy()
    if require_positive_clv:
        sig = sig[(sig["clv_120s"] >= 0) | (sig["clv_300s"] >= 0)].copy()
    if sig.empty:
        return sig
    return sig.sort_values("timestamp_ns").drop_duplicates("match_id", keep="first").copy()


def pnl_for(trades: pd.DataFrame, slip_cents: int, stake_usd: float = 5.0) -> pd.Series:
    fill = trades["ask"] + slip_cents / 100.0
    shares = stake_usd / fill
    return pd.Series(np.where(trades["settlement_binary"].eq(1.0), shares - stake_usd, -stake_usd), index=trades.index)


def trade_metrics(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {
            "trades": 0,
            "matches": 0,
            "win_rate": None,
            "avg_ask": None,
            "avg_edge": None,
            "avg_clv_120s": None,
            "avg_clv_300s": None,
            "avg_clv_900s": None,
            "slippage_roi": {str(c): None for c in SLIPPAGE_CENTS},
            "ex_best_roi": {"1": None, "3": None, "5": None},
        }
    out = {
        "trades": int(len(trades)),
        "matches": int(trades["match_id"].nunique()),
        "win_rate": float(trades["settlement_binary"].mean()),
        "avg_ask": float(trades["ask"].mean()),
        "avg_edge": float(trades["model_edge"].mean()),
        "avg_clv_120s": float(trades["clv_120s"].mean()),
        "avg_clv_300s": float(trades["clv_300s"].mean()),
        "avg_clv_900s": float(trades["clv_900s"].mean()),
        "slippage_roi": {},
        "ex_best_roi": {},
    }
    for cents in SLIPPAGE_CENTS:
        pnl = pnl_for(trades, cents)
        out["slippage_roi"][str(cents)] = float(pnl.sum() / (5.0 * len(trades)))
    pnl0 = pnl_for(trades, 0).sort_values(ascending=False)
    for k in (1, 3, 5):
        if len(pnl0) > k:
            out["ex_best_roi"][str(k)] = float(pnl0.iloc[k:].sum() / (5.0 * (len(pnl0) - k)))
        else:
            out["ex_best_roi"][str(k)] = None
    return out


def bootstrap_roi_ci(trades: pd.DataFrame, *, slip_cents: int, samples: int = 5000) -> list[float | None]:
    if trades.empty:
        return [None, None, None]
    rng = np.random.default_rng(1811 + slip_cents)
    idx = np.arange(len(trades))
    rois = np.empty(samples)
    for i in range(samples):
        sample = trades.iloc[rng.choice(idx, size=len(idx), replace=True)]
        rois[i] = float(pnl_for(sample, slip_cents).sum() / (5.0 * len(sample)))
    return [float(x) for x in np.quantile(rois, [0.025, 0.5, 0.975])]


def bucket_metrics(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {}
    out: dict[str, list[dict]] = {}
    bucket_defs = {
        "ask": ("ask", [0.0, 0.4, 0.6, 0.8, 1.0]),
        "game_time": ("game_time_sec", [0, 600, 1200, 1800, 2400, 10000]),
        "lead": ("token_net_worth_lead", [-100000, 0, 1000, 3000, 100000]),
        "edge": ("model_edge", [0, 0.04, 0.08, 0.12, 1.0]),
    }
    for name, (column, bins) in bucket_defs.items():
        tmp = trades.copy()
        tmp["bucket"] = pd.cut(tmp[column], bins=bins)
        rows = []
        for bucket, group in tmp.groupby("bucket", observed=False):
            if group.empty:
                continue
            rows.append(
                {
                    "bucket": str(bucket),
                    "trades": int(len(group)),
                    "win_rate": float(group["settlement_binary"].mean()),
                    "roi_0c": float(pnl_for(group, 0).sum() / (5.0 * len(group))),
                    "roi_4c": float(pnl_for(group, 4).sum() / (5.0 * len(group))),
                    "avg_clv_300s": float(group["clv_300s"].mean()),
                }
            )
        out[name] = rows
    return out


def promotion_verdict(metrics: dict) -> dict:
    trades = metrics["trades"]
    roi_4c = metrics["slippage_roi"].get("4")
    ex_best_3 = metrics["ex_best_roi"].get("3")
    clv_300 = metrics["avg_clv_300s"]
    checks = {
        "min_50_trades": trades >= 50,
        "roi_4c_positive": roi_4c is not None and roi_4c > 0,
        "ex_best_3_nonnegative": ex_best_3 is not None and ex_best_3 >= 0,
        "clv_300_positive": clv_300 is not None and clv_300 > 0,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "recommendation": "paper_only" if not all(checks.values()) else "eligible_for_forward_paper_soak",
    }


def threshold_sweep(scored: pd.DataFrame, *, slippage_for_gate: float, require_positive_clv: bool) -> list[dict]:
    rows = []
    for threshold in [0.01, 0.02, 0.03, 0.04, 0.05, 0.075, 0.10, 0.125]:
        trades = first_trades(
            scored,
            edge_threshold=threshold,
            slippage_for_gate=slippage_for_gate,
            require_positive_clv=require_positive_clv,
        )
        metrics = trade_metrics(trades)
        rows.append(
            {
                "edge_threshold": threshold,
                "trades": metrics["trades"],
                "win_rate": metrics["win_rate"],
                "roi_0c": metrics["slippage_roi"].get("0"),
                "roi_4c": metrics["slippage_roi"].get("4"),
                "ex_best_3": metrics["ex_best_roi"].get("3"),
                "avg_clv_300s": metrics["avg_clv_300s"],
            }
        )
    return rows


def prediction_metrics(scored: pd.DataFrame) -> dict:
    if scored.empty:
        return {}
    y = scored["settlement_binary"].to_numpy()
    p = scored["p_model"].to_numpy()
    mid = scored["market_mid"].to_numpy()
    out = {
        "rows": int(len(scored)),
        "matches": int(scored["match_id"].nunique()),
        "brier_model": float(brier_score_loss(y, p)),
        "brier_market_mid": float(brier_score_loss(y, mid)),
        "residual_rmse": float(mean_squared_error(scored["settlement_binary"] - scored["market_mid"], scored["pred_residual"]) ** 0.5),
        "residual_mae": float(mean_absolute_error(scored["settlement_binary"] - scored["market_mid"], scored["pred_residual"])),
    }
    try:
        out["auc_model"] = float(roc_auc_score(y, p))
        out["auc_market_mid"] = float(roc_auc_score(y, mid))
    except ValueError:
        out["auc_model"] = None
        out["auc_market_mid"] = None
    return out


def run_walk_forward(
    df: pd.DataFrame,
    *,
    features: list[str],
    model_type: str,
    shrink: float,
    min_train_matches: int,
    folds: int,
) -> tuple[pd.DataFrame, list[dict]]:
    fold_defs = chronological_folds(df, min_train_matches=min_train_matches, folds=folds)
    scored_frames: list[pd.DataFrame] = []
    fold_reports: list[dict] = []
    for i, (train_matches, valid_matches) in enumerate(fold_defs, start=1):
        train = df[df["match_id"].isin(train_matches)].copy()
        valid = df[df["match_id"].isin(valid_matches)].copy()
        if train.empty or valid.empty:
            continue
        model = fit_model(train, features, model_type)
        scored = add_predictions(valid, model, features, shrink)
        scored["fold"] = i
        scored_frames.append(scored)
        fold_reports.append(
            {
                "fold": i,
                "train_matches": int(len(train_matches)),
                "valid_matches": int(len(valid_matches)),
                "train_rows": int(len(train)),
                "valid_rows": int(len(valid)),
                "prediction_metrics": prediction_metrics(scored),
            }
        )
    if not scored_frames:
        return pd.DataFrame(), fold_reports
    return pd.concat(scored_frames, ignore_index=True), fold_reports


def simple_rule_predictions(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    # A conservative hand baseline: gettoplive lead is already positive, not just improving.
    score = (
        0.000012 * out["token_net_worth_lead"]
        + 0.00008 * out["lead_delta_30s"]
        + 0.015 * out["token_score_margin"]
        + 0.01 * np.sign(out["mid_delta_30s"].clip(-0.1, 0.1))
    )
    out["pred_residual"] = score.clip(-0.20, 0.20)
    out["p_model"] = np.clip(out["market_mid"] + out["pred_residual"], 0.0, 1.0)
    out["model_edge"] = out["p_model"] - out["ask"]
    out["fold"] = 0
    return out


def write_report(report: dict, path: Path) -> None:
    lines = ["# Model Value V2 Walk-Forward Evaluation\n"]
    settings = report["settings"]
    lines.append("## Settings")
    for key, value in settings.items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    for name, metrics in report["strategies"].items():
        lines.append(f"## {name}")
        pred = metrics["prediction_metrics"]
        if pred:
            lines.append(f"- Rows: {pred['rows']}")
            lines.append(f"- Matches: {pred['matches']}")
            lines.append(f"- Brier model: {pred['brier_model']:.6f}")
            lines.append(f"- Brier market_mid: {pred['brier_market_mid']:.6f}")
            lines.append(f"- AUC model: {pred['auc_model']}")
            lines.append(f"- AUC market_mid: {pred['auc_market_mid']}")
        trades = metrics["trade_metrics"]
        lines.append(f"- Trades: {trades['trades']}")
        lines.append(f"- Win rate: {trades['win_rate'] if trades['win_rate'] is not None else 'n/a'}")
        lines.append(f"- Avg ask: {trades['avg_ask'] if trades['avg_ask'] is not None else 'n/a'}")
        lines.append(f"- Avg edge: {trades['avg_edge'] if trades['avg_edge'] is not None else 'n/a'}")
        lines.append(f"- Avg CLV 120s: {trades['avg_clv_120s'] if trades['avg_clv_120s'] is not None else 'n/a'}")
        lines.append(f"- Avg CLV 300s: {trades['avg_clv_300s'] if trades['avg_clv_300s'] is not None else 'n/a'}")
        lines.append("")
        lines.append("| slippage_cents | ROI |")
        lines.append("|---:|---:|")
        for cents, roi in trades["slippage_roi"].items():
            lines.append(f"| {cents} | {roi if roi is not None else 'n/a'} |")
        lines.append("")
        lines.append("| ex_best_n | ROI |")
        lines.append("|---:|---:|")
        for k, roi in trades["ex_best_roi"].items():
            lines.append(f"| {k} | {roi if roi is not None else 'n/a'} |")
        lines.append("")
        verdict = metrics["promotion_verdict"]
        lines.append("### Promotion Verdict")
        lines.append(f"- Recommendation: {verdict['recommendation']}")
        lines.append(f"- Passed: {verdict['passed']}")
        for key, value in verdict["checks"].items():
            lines.append(f"- {key}: {value}")
        lines.append("")
        lines.append("### Threshold Sweep")
        lines.append("| edge | trades | roi_0c | roi_4c | ex_best_3 | clv_300s |")
        lines.append("|---:|---:|---:|---:|---:|---:|")
        for row in metrics["threshold_sweep"]:
            lines.append(
                f"| {row['edge_threshold']} | {row['trades']} | {row['roi_0c']} | "
                f"{row['roi_4c']} | {row['ex_best_3']} | {row['avg_clv_300s']} |"
            )
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data_v2/model_value_dataset_v2.parquet")
    parser.add_argument("--out-dir", default="reports/model_value_v2_walk_forward")
    parser.add_argument("--model-type", choices=["hgb", "ridge"], default="hgb")
    parser.add_argument("--shrink", type=float, default=0.5)
    parser.add_argument("--edge-threshold", type=float, default=0.02)
    parser.add_argument("--gate-slippage-cents", type=float, default=4.0)
    parser.add_argument("--min-train-matches", type=int, default=20)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--require-positive-clv", action="store_true")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    df = pd.read_parquet(dataset_path) if dataset_path.suffix == ".parquet" else pd.read_csv(dataset_path)
    missing = [col for col in FEATURE_COLUMNS if col not in df.columns]
    if missing:
        raise RuntimeError(f"Dataset missing v2 feature columns: {', '.join(missing)}")
    df = df.dropna(subset=FEATURE_COLUMNS + ["settlement_binary", "ask", "market_mid"]).copy()
    df["match_id"] = df["match_id"].astype(str)
    df = df.sort_values(["timestamp_ns", "match_id", "token_id"]).reset_index(drop=True)

    scored, fold_reports = run_walk_forward(
        df,
        features=FEATURE_COLUMNS,
        model_type=args.model_type,
        shrink=args.shrink,
        min_train_matches=args.min_train_matches,
        folds=args.folds,
    )
    rule_scored = simple_rule_predictions(df)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    strategies: dict[str, dict] = {}
    for name, frame in {
        f"walk_forward_{args.model_type}": scored,
        "lead_velocity_rule": rule_scored,
    }.items():
        trades = first_trades(
            frame,
            edge_threshold=args.edge_threshold,
            slippage_for_gate=args.gate_slippage_cents / 100.0,
            require_positive_clv=args.require_positive_clv,
        )
        frame.to_csv(out_dir / f"{name}_scored.csv", index=False)
        trades.to_csv(out_dir / f"{name}_trades.csv", index=False)
        t_metrics = trade_metrics(trades)
        strategies[name] = {
            "prediction_metrics": prediction_metrics(frame),
            "trade_metrics": t_metrics,
            "bootstrap_roi_ci": {
                "0c": bootstrap_roi_ci(trades, slip_cents=0),
                "4c": bootstrap_roi_ci(trades, slip_cents=4),
            },
            "bucket_metrics": bucket_metrics(trades),
            "threshold_sweep": threshold_sweep(
                frame,
                slippage_for_gate=args.gate_slippage_cents / 100.0,
                require_positive_clv=args.require_positive_clv,
            ),
            "promotion_verdict": promotion_verdict(t_metrics),
        }

    report = {
        "settings": {
            "dataset": str(dataset_path),
            "rows": int(len(df)),
            "matches": int(df["match_id"].nunique()),
            "model_type": args.model_type,
            "shrink": args.shrink,
            "edge_threshold": args.edge_threshold,
            "gate_slippage_cents": args.gate_slippage_cents,
            "min_train_matches": args.min_train_matches,
            "folds": args.folds,
            "require_positive_clv": args.require_positive_clv,
            "features": FEATURE_COLUMNS,
        },
        "folds": fold_reports,
        "strategies": strategies,
    }
    (out_dir / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_report(report, out_dir / "summary.md")
    print(json.dumps(report["strategies"], indent=2))


if __name__ == "__main__":
    main()
