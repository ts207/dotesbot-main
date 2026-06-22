#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd


SLIPPAGE_CENTS = [0, 1, 2, 3, 4, 5, 8]


@dataclass(frozen=True)
class RuleSpec:
    name: str
    state_min: float
    lag_min: float
    max_price_response: float
    min_lead_delta_30s: float
    min_score_delta_30s: float | None
    min_ask: float
    max_ask: float
    require_wrong_way_or_flat: bool
    require_current_lead: bool


def _required_columns() -> list[str]:
    cols = [
        "timestamp_ns",
        "match_id",
        "token_id",
        "ask",
        "market_mid",
        "token_net_worth_lead",
        "lead_delta_30s",
        "score_delta_30s",
        "state_move_score",
        "price_response_score",
        "reaction_lag_score",
        "wrong_way_or_flat_price",
        "clv_120s",
        "clv_300s",
        "settlement_binary",
    ]
    return cols


def load_dataset(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    missing = [col for col in _required_columns() if col not in df.columns]
    if missing:
        raise RuntimeError(f"Dataset missing reaction-lag columns: {', '.join(missing)}")
    df = df.copy()
    df["match_id"] = df["match_id"].astype(str)
    df["token_id"] = df["token_id"].astype(str)
    for col in _required_columns():
        if col not in {"match_id", "token_id", "wrong_way_or_flat_price"}:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["wrong_way_or_flat_price"] = df["wrong_way_or_flat_price"].astype(bool)
    if "clv_900s" not in df.columns:
        df["clv_900s"] = np.nan
    df = df.dropna(subset=_required_columns()).copy()
    return df.sort_values(["timestamp_ns", "match_id", "token_id"]).reset_index(drop=True)


def generate_rule_specs() -> list[RuleSpec]:
    specs: list[RuleSpec] = []
    for state_min in (0.02, 0.04, 0.06, 0.08):
        for lag_min in (0.02, 0.04, 0.06, 0.08):
            for max_price_response in (-0.02, 0.00, 0.02, 0.04):
                for min_lead_delta in (500.0, 1000.0, 1500.0):
                    for max_ask in (0.45, 0.60, 0.80):
                        for require_wrong_way in (False, True):
                            name = (
                                f"state{state_min:.2f}_lag{lag_min:.2f}_pxlte{max_price_response:.2f}_"
                                f"lead{int(min_lead_delta)}_ask{max_ask:.2f}_"
                                f"{'wrongway' if require_wrong_way else 'under'}"
                            )
                            specs.append(
                                RuleSpec(
                                    name=name,
                                    state_min=state_min,
                                    lag_min=lag_min,
                                    max_price_response=max_price_response,
                                    min_lead_delta_30s=min_lead_delta,
                                    min_score_delta_30s=0.0,
                                    min_ask=0.08,
                                    max_ask=max_ask,
                                    require_wrong_way_or_flat=require_wrong_way,
                                    require_current_lead=False,
                                )
                            )
    return specs


def select_candidates(df: pd.DataFrame, spec: RuleSpec) -> pd.DataFrame:
    mask = (
        (df["state_move_score"] >= spec.state_min)
        & (df["reaction_lag_score"] >= spec.lag_min)
        & (df["price_response_score"] <= spec.max_price_response)
        & (df["lead_delta_30s"] >= spec.min_lead_delta_30s)
        & (df["ask"].between(spec.min_ask, spec.max_ask, inclusive="both"))
    )
    if spec.min_score_delta_30s is not None:
        mask &= df["score_delta_30s"] >= spec.min_score_delta_30s
    if spec.require_wrong_way_or_flat:
        mask &= df["wrong_way_or_flat_price"]
    if spec.require_current_lead:
        mask &= df["token_net_worth_lead"] > 0
    return df.loc[mask].copy()


def first_per_match(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return candidates
    return candidates.sort_values(["timestamp_ns", "match_id", "token_id"]).drop_duplicates("match_id", keep="first").copy()


def exit_roi(trades: pd.DataFrame, horizon_sec: int, slip_cents: int) -> pd.Series:
    fill = trades["ask"] + slip_cents / 100.0
    future_mid = trades["ask"] + trades[f"clv_{horizon_sec}s"]
    return pd.Series(np.where(fill > 0, future_mid / fill - 1.0, np.nan), index=trades.index)


def settlement_roi(trades: pd.DataFrame, slip_cents: int, stake_usd: float = 5.0) -> pd.Series:
    fill = trades["ask"] + slip_cents / 100.0
    shares = stake_usd / fill
    pnl = np.where(trades["settlement_binary"].eq(1.0), shares - stake_usd, -stake_usd)
    return pd.Series(pnl / stake_usd, index=trades.index)


def ex_best_mean(values: pd.Series, n: int) -> float | None:
    clean = values.dropna().sort_values(ascending=False)
    if len(clean) <= n:
        return None
    return float(clean.iloc[n:].mean())


def bootstrap_ci(values: pd.Series, samples: int = 3000) -> list[float | None]:
    clean = values.dropna().to_numpy(dtype=float)
    if len(clean) == 0:
        return [None, None, None]
    rng = np.random.default_rng(4117)
    idx = np.arange(len(clean))
    means = np.empty(samples)
    for i in range(samples):
        means[i] = float(clean[rng.choice(idx, size=len(idx), replace=True)].mean())
    return [float(x) for x in np.quantile(means, [0.025, 0.5, 0.975])]


def metrics_for(trades: pd.DataFrame, *, include_bootstrap: bool = False) -> dict:
    if trades.empty:
        return {
            "trades": 0,
            "matches": 0,
            "avg_ask": None,
            "settlement_win_rate": None,
            "avg_state_move_score": None,
            "avg_price_response_score": None,
            "avg_reaction_lag_score": None,
            "avg_clv_120s": None,
            "avg_clv_300s": None,
            "exit_roi": {},
            "settlement_roi": {},
            "ex_best_exit_roi_300s": {},
            "bootstrap_exit_roi_300s_02c": [None, None, None],
        }
    out = {
        "trades": int(len(trades)),
        "matches": int(trades["match_id"].nunique()),
        "avg_ask": float(trades["ask"].mean()),
        "settlement_win_rate": float(trades["settlement_binary"].mean()),
        "avg_state_move_score": float(trades["state_move_score"].mean()),
        "avg_price_response_score": float(trades["price_response_score"].mean()),
        "avg_reaction_lag_score": float(trades["reaction_lag_score"].mean()),
        "avg_clv_120s": float(trades["clv_120s"].mean()),
        "avg_clv_300s": float(trades["clv_300s"].mean()),
        "exit_roi": {},
        "settlement_roi": {},
        "ex_best_exit_roi_300s": {},
    }
    for horizon in (120, 300):
        out["exit_roi"][str(horizon)] = {}
        for cents in SLIPPAGE_CENTS:
            out["exit_roi"][str(horizon)][str(cents)] = float(exit_roi(trades, horizon, cents).mean())
    for cents in SLIPPAGE_CENTS:
        out["settlement_roi"][str(cents)] = float(settlement_roi(trades, cents).mean())
        out["ex_best_exit_roi_300s"][str(cents)] = ex_best_mean(exit_roi(trades, 300, cents), 3)
    out["bootstrap_exit_roi_300s_02c"] = (
        bootstrap_ci(exit_roi(trades, 300, 2))
        if include_bootstrap
        else [None, None, None]
    )
    return out


def score_rule(df: pd.DataFrame, spec: RuleSpec) -> dict:
    candidates = select_candidates(df, spec)
    trades = first_per_match(candidates)
    metrics = metrics_for(trades)
    return {
        "rule": asdict(spec),
        "candidate_rows": int(len(candidates)),
        "candidate_matches": int(candidates["match_id"].nunique()) if not candidates.empty else 0,
        "metrics": metrics,
    }


def rule_passes(metrics: dict, *, min_trades: int) -> dict:
    roi_300_2c = metrics["exit_roi"].get("300", {}).get("2")
    roi_300_4c = metrics["exit_roi"].get("300", {}).get("4")
    ex_best_2c = metrics["ex_best_exit_roi_300s"].get("2")
    ci_2c = metrics.get("bootstrap_exit_roi_300s_02c") or [None, None, None]
    checks = {
        "min_trades": metrics["trades"] >= min_trades,
        "roi_300s_2c_positive": roi_300_2c is not None and roi_300_2c > 0,
        "roi_300s_4c_nonnegative": roi_300_4c is not None and roi_300_4c >= 0,
        "ex_best_3_300s_2c_nonnegative": ex_best_2c is not None and ex_best_2c >= 0,
        "bootstrap_median_300s_2c_positive": ci_2c[1] is not None and ci_2c[1] > 0,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "recommendation": "research_forward_paper" if all(checks.values()) else "do_not_trade_live",
    }


def top_rows(results: list[dict], limit: int) -> list[dict]:
    rows = []
    for result in results:
        m = result["metrics"]
        if m["trades"] == 0:
            continue
        rows.append(
            {
                "name": result["rule"]["name"],
                "trades": m["trades"],
                "candidate_rows": result["candidate_rows"],
                "avg_ask": m["avg_ask"],
                "avg_clv_120s": m["avg_clv_120s"],
                "avg_clv_300s": m["avg_clv_300s"],
                "exit_roi_300s_2c": m["exit_roi"]["300"]["2"],
                "exit_roi_300s_4c": m["exit_roi"]["300"]["4"],
                "ex_best_3_300s_2c": m["ex_best_exit_roi_300s"]["2"],
                "settlement_roi_2c": m["settlement_roi"]["2"],
            }
        )
    return sorted(
        rows,
        key=lambda r: (
            r["trades"] >= 20,
            r["exit_roi_300s_2c"],
            r["ex_best_3_300s_2c"] if r["ex_best_3_300s_2c"] is not None else -999.0,
        ),
        reverse=True,
    )[:limit]


def bucket_diagnostics(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {}
    out: dict[str, list[dict]] = {}
    buckets = {
        "ask": ("ask", [0.0, 0.25, 0.45, 0.60, 0.80, 1.0]),
        "state_move_score": ("state_move_score", [0.0, 0.04, 0.08, 0.12, 1.0]),
        "reaction_lag_score": ("reaction_lag_score", [0.0, 0.04, 0.08, 0.12, 1.0]),
        "game_time": ("game_time_sec", [0, 600, 1200, 1800, 2400, 10000]),
    }
    for name, (column, bins) in buckets.items():
        frame = trades.copy()
        frame["bucket"] = pd.cut(frame[column], bins=bins)
        rows = []
        for bucket, group in frame.groupby("bucket", observed=False):
            if group.empty:
                continue
            rows.append(
                {
                    "bucket": str(bucket),
                    "trades": int(len(group)),
                    "avg_clv_300s": float(group["clv_300s"].mean()),
                    "exit_roi_300s_2c": float(exit_roi(group, 300, 2).mean()),
                    "settlement_roi_2c": float(settlement_roi(group, 2).mean()),
                }
            )
        out[name] = rows
    return out


def write_report(report: dict, path: Path) -> None:
    lines = ["# Reaction Lag V1 Evaluation\n"]
    settings = report["settings"]
    lines.append("## Settings")
    for key, value in settings.items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("## Best Rule")
    best = report["best_rule"]
    if best is None:
        lines.append("- No rules produced trades.")
    else:
        rule = best["rule"]
        metrics = best["metrics"]
        verdict = best["verdict"]
        lines.append(f"- Name: {rule['name']}")
        lines.append(f"- Trades: {metrics['trades']}")
        lines.append(f"- Candidate rows: {best['candidate_rows']}")
        lines.append(f"- Avg ask: {metrics['avg_ask']}")
        lines.append(f"- Avg CLV 120s: {metrics['avg_clv_120s']}")
        lines.append(f"- Avg CLV 300s: {metrics['avg_clv_300s']}")
        lines.append(f"- Exit ROI 300s after 2c: {metrics['exit_roi']['300']['2']}")
        lines.append(f"- Exit ROI 300s after 4c: {metrics['exit_roi']['300']['4']}")
        lines.append(f"- Ex-best-3 exit ROI 300s after 2c: {metrics['ex_best_exit_roi_300s']['2']}")
        lines.append(f"- Settlement ROI after 2c: {metrics['settlement_roi']['2']}")
        lines.append(f"- Bootstrap 300s 2c ROI CI: {metrics['bootstrap_exit_roi_300s_02c']}")
        lines.append(f"- Recommendation: {verdict['recommendation']}")
        for key, value in verdict["checks"].items():
            lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("## Top Rules")
    lines.append("| rule | trades | rows | ask | clv_120s | clv_300s | roi_300_2c | roi_300_4c | ex_best_3_2c | settle_roi_2c |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row in report["top_rules"]:
        lines.append(
            f"| {row['name']} | {row['trades']} | {row['candidate_rows']} | {row['avg_ask']} | "
            f"{row['avg_clv_120s']} | {row['avg_clv_300s']} | {row['exit_roi_300s_2c']} | "
            f"{row['exit_roi_300s_4c']} | {row['ex_best_3_300s_2c']} | {row['settlement_roi_2c']} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data_v2/reaction_lag_dataset_v1.parquet")
    parser.add_argument("--out-dir", default="reports/reaction_lag_v1")
    parser.add_argument("--min-trades", type=int, default=20)
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    df = load_dataset(dataset_path)
    results = [score_rule(df, spec) for spec in generate_rule_specs()]
    ranked = top_rows(results, args.top)
    best = None
    if ranked:
        by_name = {result["rule"]["name"]: result for result in results}
        best = by_name[ranked[0]["name"]]
        best = dict(best)
        best_trades = first_per_match(select_candidates(df, RuleSpec(**best["rule"])))
        best["metrics"] = metrics_for(best_trades, include_bootstrap=True)
        best["verdict"] = rule_passes(best["metrics"], min_trades=args.min_trades)
        best["bucket_diagnostics"] = bucket_diagnostics(best_trades)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if best is not None:
        best_trades = first_per_match(select_candidates(df, RuleSpec(**best["rule"])))
        best_trades.to_csv(out_dir / "best_rule_trades.csv", index=False)
    all_rows = []
    for result in results:
        verdict = rule_passes(result["metrics"], min_trades=args.min_trades)
        row = {
            "rule": result["rule"]["name"],
            "candidate_rows": result["candidate_rows"],
            "candidate_matches": result["candidate_matches"],
            "trades": result["metrics"]["trades"],
            "avg_ask": result["metrics"]["avg_ask"],
            "avg_clv_120s": result["metrics"]["avg_clv_120s"],
            "avg_clv_300s": result["metrics"]["avg_clv_300s"],
            "exit_roi_300s_2c": result["metrics"]["exit_roi"].get("300", {}).get("2"),
            "exit_roi_300s_4c": result["metrics"]["exit_roi"].get("300", {}).get("4"),
            "ex_best_3_300s_2c": result["metrics"]["ex_best_exit_roi_300s"].get("2"),
            "settlement_roi_2c": result["metrics"]["settlement_roi"].get("2"),
            "passed": verdict["passed"],
        }
        all_rows.append(row)
    pd.DataFrame(all_rows).sort_values(
        ["trades", "exit_roi_300s_2c"],
        ascending=[False, False],
    ).to_csv(out_dir / "rule_sweep.csv", index=False)

    report = {
        "settings": {
            "dataset": str(dataset_path),
            "rows": int(len(df)),
            "matches": int(df["match_id"].nunique()),
            "rules_evaluated": int(len(results)),
            "min_trades": args.min_trades,
            "dedupe": "first qualifying signal per match",
            "primary_metric": "300s exit ROI after 2c/4c slippage",
            "secondary_metric": "settlement ROI",
        },
        "best_rule": best,
        "top_rules": ranked,
    }
    (out_dir / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_report(report, out_dir / "summary.md")
    print(json.dumps({"best_rule": best, "top_rules": ranked[:5]}, indent=2))


if __name__ == "__main__":
    main()
