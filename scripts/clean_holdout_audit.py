import sys
import pandas as pd
import numpy as np
import json
import os
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.train_model_value_edge_v1 import chronological_match_split
from model_value_engine import ModelValueEngine
import model_value_predictor
from scripts.evaluate_trades import evaluate_trades
from scripts.backtest_model_value_edge_v1 import (
    build_game, build_mapping, build_book_store,
    signal_to_row, reject_to_row
)
from config import RUNTIME_CONFIG, MODEL_VALUE_MODEL_PATH

def get_train_val_matches(df: pd.DataFrame) -> set:
    match_times = df.groupby("match_id")["timestamp_ns"].min().sort_values()
    return set(match_times.iloc[:95].index)

def run_simulation(df: pd.DataFrame, threshold: float, out_dir: Path):
    os.environ["MODEL_VALUE_MIN_EDGE"] = str(threshold)
    os.environ["MODEL_VALUE_CONFIRM_MIN_EDGE"] = str(threshold)
    os.environ["MODEL_VALUE_MAX_SPREAD"] = "0.05"
    os.environ["MODEL_VALUE_CONFIRMATION_MAX_ASK_WORSEN"] = "0.00"
    
    # Reload config manually or override constants if needed, but ModelValueEngine 
    # uses config constants directly. Actually, the easiest way to override is to 
    # modify the RUNTIME_CONFIG singleton and `model_value_engine` globals.
    import model_value_engine
    model_value_engine.MODEL_VALUE_MIN_EDGE = threshold
    model_value_engine.MODEL_VALUE_CONFIRM_MIN_EDGE = threshold
    model_value_engine.MODEL_VALUE_MAX_SPREAD = 0.05
    model_value_engine.MODEL_VALUE_CONFIRMATION_MAX_AGE_SEC = 30.0
    model_value_engine.MODEL_VALUE_CONFIRMATION_MAX_ASK_WORSEN = 0.0
    model_value_engine.MODEL_VALUE_ENABLE_CONFIRMATION = True
    
    engine = ModelValueEngine()
    
    signals = []
    entered_matches = set()
    all_raw_signals = []
    
    df = df.sort_values("timestamp_ns")
    
    for idx, row in df.iterrows():
        game = build_game(row)
        mapping = build_mapping(row)
        book_store = build_book_store(row)
        
        # Enforce exact production gates globally per match
        match_id = str(row["match_id"])
        
        # In actual run we pass entered_tokens. We will use a proxy for one trade per match.
        results = engine.evaluate(game, mapping, book_store, entered_tokens=set())
        
        # Check raw predictor output for clipping (re-run predict manually to see details)
        for side in ["YES", "NO"]:
            features = model_value_predictor.build_side_features(game, mapping, side, book_store[str(row[f"{side.lower()}_token_id"])])
            if features:
                pred = model_value_predictor.predict_probability(features)
                if pred["features_available"]:
                    p_model = pred["model_probability"]
                    all_raw_signals.append({
                        "match_id": match_id,
                        "token_id": row[f"{side.lower()}_token_id"],
                        "timestamp_ns": row["timestamp_ns"],
                        "p_model": p_model,
                        "market_mid": features["market_mid"],
                        "is_clipped": p_model <= 0.0 or p_model >= 1.0,
                        "has_net_worth": not np.isnan(features.get("token_net_worth_lead", np.nan))
                    })

        for res in results:
            if hasattr(res, 'decision') and res.decision == 'signal':
                # Production gate: only 1 trade per match allowed
                if match_id in entered_matches:
                    continue
                
                if res.confirmed:
                    sig_row = signal_to_row(res, row)
                    sig_row["is_clipped"] = res.model_probability <= 0.0 or res.model_probability >= 1.0
                    signals.append(sig_row)
                    entered_matches.add(match_id)
                    
    signals_df = pd.DataFrame(signals)
    raw_signals_df = pd.DataFrame(all_raw_signals)
    
    if not signals_df.empty:
        signals_df.to_csv(out_dir / "trades.csv", index=False)
    if not raw_signals_df.empty:
        raw_signals_df.to_csv(out_dir / "raw_signals.csv", index=False)
        
    return signals_df, raw_signals_df

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", default="data_v2/model_value_replay.parquet")
    parser.add_argument("--out-dir", default="reports/clean_holdout_audit")
    args = parser.parse_args()
    
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    df = pd.read_parquet(args.replay)
    
    train_val_matches = get_train_val_matches(df)
    holdout_df = df[~df["match_id"].isin(train_val_matches)].copy()
    
    print(f"Total replay rows: {len(df)}")
    print(f"Holdout rows: {len(holdout_df)}")
    print(f"Holdout matches: {holdout_df['match_id'].nunique()}")
    
    results = {}
    
    for th in [0.01, 0.02]:
        th_dir = out_dir / f"th_{th}"
        th_dir.mkdir(exist_ok=True)
        
        print(f"Running simulation for threshold {th}...")
        trades_df, raw_signals_df = run_simulation(holdout_df, th, th_dir)
        
        if trades_df.empty:
            print(f"No trades for threshold {th}")
            continue
            
        print(f"Evaluating trades for threshold {th}...")
        metrics = evaluate_trades(trades_df, holdout_df, th_dir)
        results[th] = {
            "trades": len(trades_df),
            "metrics": metrics,
            "raw_signals": raw_signals_df,
            "trades_df": trades_df
        }
        
    report_lines = [
        "# Clean Holdout Audit Report",
        f"Holdout matches: {holdout_df['match_id'].nunique()}",
        f"Holdout rows: {len(holdout_df)}",
        ""
    ]
    
    for th, res in results.items():
        report_lines.append(f"## Threshold {th}")
        metrics = res["metrics"]
        trades = res["trades_df"]
        
        res_roi = metrics.get("resolved_roi", 0.0)
        report_lines.append(f"- Trades: {res['trades']}")
        report_lines.append(f"- Settlement ROI: {res_roi:.2%}")
        
        clv_1200s = metrics.get("clv_1200s", 0.0)
        clv_900s = metrics.get("clv_900s", 0.0)
        report_lines.append(f"- CLV_900s: {clv_900s:.4f}")
        report_lines.append(f"- CLV_1200s: {clv_1200s:.4f}")
        
        if "settlement_outcome" in trades.columns and "entry_ask" in trades.columns:
            # Reconstruct the profit explicitly to avoid issues
            valid_trades = trades[trades["settlement_outcome"].isin(["WIN", "LOSS"])]
            stake = 5.0
            def pnl(row):
                return (1.0 - row["entry_ask"]) * stake if row["settlement_outcome"] == "WIN" else -row["entry_ask"] * stake
            if not valid_trades.empty:
                profits = valid_trades.apply(pnl, axis=1)
                sorted_profits = profits.sort_values(ascending=False).values
                if len(sorted_profits) > 3:
                    total_cost = valid_trades["entry_ask"].sum() * stake
                    total_profit = sum(sorted_profits)
                    best_3_cost = valid_trades.loc[profits.nlargest(3).index, "entry_ask"].sum() * stake
                    roi_ex_3 = (total_profit - sum(sorted_profits[:3])) / (total_cost - best_3_cost) if (total_cost - best_3_cost) > 0 else 0
                    report_lines.append(f"- ROI excluding best 3 trades: {roi_ex_3:.2%}")
            
        clipped_trades = trades[trades["is_clipped"]]
        unclipped_trades = trades[~trades["is_clipped"]]
        
        report_lines.append(f"### Clipping Analysis (Threshold {th})")
        report_lines.append(f"- Clipped trades: {len(clipped_trades)}")
        report_lines.append(f"- Unclipped trades: {len(unclipped_trades)}")
        
        raw_sigs = res["raw_signals"]
        report_lines.append(f"- Total raw signals evaluated: {len(raw_sigs)}")
        if not raw_sigs.empty:
            report_lines.append(f"- Raw signals clipped to 0/1: {raw_sigs['is_clipped'].sum()}")
            
        # Net worth analysis
        has_nw = trades[trades["token_net_worth_lead"].notna()]
        no_nw = trades[trades["token_net_worth_lead"].isna()]
        report_lines.append(f"### Net Worth Analysis (Threshold {th})")
        report_lines.append(f"- Trades WITH net worth: {len(has_nw)}")
        report_lines.append(f"- Trades WITHOUT net worth: {len(no_nw)}")
            
        report_lines.append("")
        
    with open(out_dir / "report.md", "w") as f:
        f.write("\n".join(report_lines))
        
    print(f"Report written to {out_dir / 'report.md'}")

if __name__ == "__main__":
    main()
