import argparse
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, Any

from model_value_engine import (
    ModelValueEngine,
    ModelValueSignal,
    ModelValueReject,
    _MODEL_VALUE_CONFIRM_STATE,
    _model_value_confirmation_passes,
)

def build_game(row) -> Dict[str, Any]:
    ds = getattr(row, "data_source", "top_live")
    if pd.isna(ds): ds = "top_live"
    return {
        "match_id": str(row.match_id),
        "data_source": ds,
        "received_at_ns": int(row.timestamp_ns),
        "game_over": bool(row.game_over) if pd.notna(row.game_over) and row.game_over in [True, "True", 1, "1"] else False,
        "game_time_sec": int(row.game_time_sec) if pd.notnull(row.game_time_sec) else 1,
        "radiant_net_worth": float(row.radiant_net_worth),
        "dire_net_worth": float(row.dire_net_worth),
        "radiant_score": float(row.radiant_score),
        "dire_score": float(row.dire_score),
        "radiant_lead": float(row.radiant_lead) if getattr(row, "radiant_lead", None) is not None and not pd.isna(getattr(row, "radiant_lead", None)) else (float(row.radiant_net_worth) - float(row.dire_net_worth) if not pd.isna(row.radiant_net_worth) else float('nan')),
        "building_state": 0,
        "tower_state": 0,
    }

def build_mapping(row) -> Dict[str, Any]:
    return {
        "market_type": str(row.market_type),
        "yes_token_id": str(row.yes_token_id),
        "no_token_id": str(row.no_token_id),
        "steam_side_mapping": str(row.steam_side_mapping),
    }

def build_book_store(row) -> Dict[str, Dict[str, Any]]:
    return {
        str(row.yes_token_id): {
            "best_bid": float(row.yes_best_bid) if pd.notnull(row.yes_best_bid) else 0.0,
            "best_ask": float(row.yes_best_ask) if pd.notnull(row.yes_best_ask) else 1.0,
            "received_at_ns": int(row.book_received_at_ns) if pd.notna(row.book_received_at_ns) else 0,
        },
        str(row.no_token_id): {
            "best_bid": float(row.no_best_bid) if pd.notnull(row.no_best_bid) else 0.0,
            "best_ask": float(row.no_best_ask) if pd.notnull(row.no_best_ask) else 1.0,
            "received_at_ns": int(row.book_received_at_ns) if pd.notna(row.book_received_at_ns) else 0,
        }
    }

def get_market_mid(row, side) -> float:
    if "market_mid" in row and pd.notnull(row.market_mid):
        return float(row.market_mid)
    if side == "YES":
        return (float(row.yes_best_bid) + float(row.yes_best_ask)) / 2.0
    else:
        return (float(row.no_best_bid) + float(row.no_best_ask)) / 2.0

def reject_to_row(res: ModelValueReject, row) -> Dict[str, Any]:
    return {
        "timestamp_ns": res.received_at_ns,
        "match_id": res.match_id,
        "token_id": res.token_id,
        "side": res.side,
        "direction": res.direction,
        "decision": "reject",
        "reject_reason": res.reason,
        "confirmation_reason": "",
        "confirmed": False,
        "model_probability": res.fair_price,
        "ask": res.ask,
        "bid": None,
        "spread": None,
        "edge": res.edge,
        "book_age_ms": res.book_age_ms,
        "game_time_sec": res.game_time_sec,
        "market_mid": get_market_mid(row, res.side) if res.side else None,
        "token_net_worth_lead": None,
        "token_score_margin": None,
        "model_version": None,
    }

def signal_to_row(res: ModelValueSignal, row) -> Dict[str, Any]:
    return {
        "timestamp_ns": res.received_at_ns,
        "match_id": res.match_id,
        "token_id": res.token_id,
        "side": res.side,
        "direction": res.direction,
        "decision": "signal",
        "reject_reason": "",
        "confirmation_reason": "",
        "confirmed": False,
        "model_probability": res.fair_price,
        "ask": res.ask,
        "bid": res.ask - res.edge if res.ask and res.edge else None,
        "spread": None,
        "edge": res.edge,
        "book_age_ms": res.book_age_ms,
        "game_time_sec": res.game_time_sec,
        "market_mid": get_market_mid(row, res.side),
        "token_net_worth_lead": res.token_net_worth_lead,
        "token_score_margin": res.token_score_margin,
        "model_version": res.model_version,
    }

def compute_clv(trades_df: pd.DataFrame, rows_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df.empty:
        for clv_col in ['clv_30s', 'clv_120s', 'clv_300s', 'clv_900s', 'clv_1200s']:
            trades_df[clv_col] = np.nan
        return trades_df
        
    trades_df = trades_df.copy()
    rows_df = rows_df.sort_values('timestamp_ns')
    
    clv_30s, clv_120s, clv_300s, clv_900s, clv_1200s, last_mids = [], [], [], [], [], []
    
    for _, trade in trades_df.iterrows():
        token_id = trade['token_id']
        entry_ns = trade['entry_timestamp_ns']
        
        future_rows = rows_df[(rows_df['timestamp_ns'] > entry_ns)]
        
        yes_match = future_rows[future_rows['yes_token_id'] == token_id]
        no_match = future_rows[future_rows['no_token_id'] == token_id]
        
        def get_future_mid(delta_sec):
            target_ns = entry_ns + int(delta_sec * 1e9)
            y_matches = yes_match[yes_match['timestamp_ns'] >= target_ns]
            n_matches = no_match[no_match['timestamp_ns'] >= target_ns]
            
            if not y_matches.empty and (n_matches.empty or y_matches.iloc[0]['timestamp_ns'] <= n_matches.iloc[0]['timestamp_ns']):
                row_f = y_matches.iloc[0]
                return (row_f.yes_best_bid + row_f.yes_best_ask) / 2.0
            elif not n_matches.empty:
                row_f = n_matches.iloc[0]
                return (row_f.no_best_bid + row_f.no_best_ask) / 2.0
            return np.nan
        
        def get_last_mid():
            y_matches = yes_match
            n_matches = no_match
            if not y_matches.empty and (n_matches.empty or y_matches.iloc[-1]['timestamp_ns'] >= n_matches.iloc[-1]['timestamp_ns']):
                row_f = y_matches.iloc[-1]
                return (row_f.yes_best_bid + row_f.yes_best_ask) / 2.0
            elif not n_matches.empty:
                row_f = n_matches.iloc[-1]
                return (row_f.no_best_bid + row_f.no_best_ask) / 2.0
            return np.nan

        clv_30s.append(get_future_mid(30) - trade['entry_ask'])
        clv_120s.append(get_future_mid(120) - trade['entry_ask'])
        clv_300s.append(get_future_mid(300) - trade['entry_ask'])
        clv_900s.append(get_future_mid(900) - trade['entry_ask'])
        clv_1200s.append(get_future_mid(1200) - trade['entry_ask'])
        last_mids.append(get_last_mid())

    trades_df['clv_30s'] = clv_30s
    trades_df['clv_120s'] = clv_120s
    trades_df['clv_300s'] = clv_300s
    trades_df['clv_900s'] = clv_900s
    trades_df['clv_1200s'] = clv_1200s
    trades_df['last_available_mid'] = last_mids
    
    return trades_df

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-file", required=True, help="Path to replay CSV")
    parser.add_argument("--out-dir", default="reports", help="Output directory")
    parser.add_argument("--no-filter", action="store_true", help="Do not filter to resolved matches")
    args = parser.parse_args()

    print(f"Loading {args.replay_file}...")
    if args.replay_file.endswith(".parquet"):
        rows = pd.read_parquet(args.replay_file)
    else:
        rows = pd.read_csv(args.replay_file)
        
    if not args.no_filter:
        print(f"Loaded {len(rows)} rows. Filtering to fully resolved matches only...")
        # Find matches where the last row for that match has a non-null settled_yes_outcome
        valid_matches = rows.groupby('dota_match_id')['settled_yes_outcome'].last().notna()
        valid_match_ids = valid_matches[valid_matches].index
        rows = rows[rows['dota_match_id'].isin(valid_match_ids)]
        print(f"Filtered to {len(rows)} rows with known terminal outcomes.")
    else:
        print(f"Loaded {len(rows)} rows. No filtering applied.")
    
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    _MODEL_VALUE_CONFIRM_STATE.clear()
    engine = ModelValueEngine()

    traded_matches = set()
    entered_tokens = set()
    signals = []
    trades = []

    print("Running engine evaluate loop...")
    for _, row in rows.sort_values("timestamp_ns").iterrows():
        # Mock time for accurate book_age_ms calculation in engine
        import time
        import unittest.mock
        with unittest.mock.patch('time.time_ns', return_value=int(row.timestamp_ns)):
            with unittest.mock.patch('time.time', return_value=int(row.timestamp_ns) / 1e9):
                game = build_game(row)
                mapping = build_mapping(row)
                book_store = build_book_store(row)
        
                results = engine.evaluate(
                    game=game,
                    mapping=mapping,
                    book_store=book_store,
                    entered_tokens=entered_tokens,
                    mode="paper_research",
                )

        for res in results:
            if isinstance(res, ModelValueReject):
                signals.append(reject_to_row(res, row))
                continue

            confirmed, reason = _model_value_confirmation_passes(res)

            signal_row = signal_to_row(res, row)
            signal_row["confirmation_reason"] = reason
            signal_row["confirmed"] = confirmed
            signals.append(signal_row)

            if not confirmed:
                continue

            if res.match_id in traded_matches:
                signals[-1]["decision"] = "reject_model_value_match_already_entered"
                continue
            
            # Find the true settlement outcome by looking at the last available row for this token
            future_token_rows = rows[(rows['yes_token_id'] == res.token_id) | (rows['no_token_id'] == res.token_id)]
            if not future_token_rows.empty:
                last_row = future_token_rows.iloc[-1]
                settlement = last_row.settled_yes_outcome if res.side == "YES" else last_row.settled_no_outcome
            else:
                settlement = row.settled_yes_outcome if res.side == "YES" else row.settled_no_outcome

            stake_usd = 5.0
            trade = {
                "entry_timestamp_ns": row.timestamp_ns,
                "match_id": res.match_id,
                "token_id": res.token_id,
                "side": res.side,
                "direction": res.direction,
                "entry_ask": res.ask,
                "stake_usd": stake_usd,
                "shares": stake_usd / res.ask if res.ask > 0 else 0,
                "model_probability": res.fair_price,
                "edge": res.edge,
                "game_time_sec": res.game_time_sec,
                "market_mid": get_market_mid(row, res.side),
                "token_net_worth_lead": res.token_net_worth_lead,
                "token_score_margin": res.token_score_margin,
                "settlement_outcome": settlement,
            }

            trades.append(trade)
            traded_matches.add(res.match_id)
            entered_tokens.add(str(res.token_id))

    print(f"Engine loop complete. Generated {len(signals)} signals and {len(trades)} trades.")
    
    signals_df = pd.DataFrame(signals)
    trades_df = pd.DataFrame(trades)

    if not trades_df.empty:
        trades_df['pnl_usd'] = trades_df.apply(
            lambda x: (x['shares'] * 1.0) - x['stake_usd'] if x['settlement_outcome'] == 'WIN' 
            else (-x['stake_usd'] if x['settlement_outcome'] == 'LOSS' else 0.0), axis=1
        )
        trades_df['roi'] = trades_df['pnl_usd'] / trades_df['stake_usd']
        

        print("Computing CLV metrics...")
        trades_df = compute_clv(trades_df, rows)
    else:
        for col in ['pnl_usd', 'roi', 'clv_30s', 'clv_120s', 'clv_300s', 'clv_900s', 'clv_1200s']:
            trades_df[col] = []

    signals_csv = out_dir / "model_value_v1_signals.csv"
    trades_csv = out_dir / "model_value_v1_trades.csv"
    signals_df.to_csv(signals_csv, index=False)
    trades_df.to_csv(trades_csv, index=False)
    
    md_path = out_dir / "model_value_v1_summary.md"
    
    if trades_df.empty:
        with open(md_path, "w") as f:
            f.write("# Model Value V1 Backtest Summary\n\nNo trades generated.\n")
    else:
        num_trades = len(trades_df)
        wins = len(trades_df[trades_df['settlement_outcome'] == 'WIN'])
        win_rate = wins / num_trades if num_trades > 0 else 0.0
        avg_ask = trades_df['entry_ask'].mean()
        avg_edge = trades_df['edge'].mean()
        total_pnl = trades_df['pnl_usd'].sum()
        total_stake = trades_df['stake_usd'].sum()
        overall_roi = total_pnl / total_stake if total_stake > 0 else 0.0
        
        avg_900s_clv = trades_df['clv_900s'].mean()
        avg_1200s_clv = trades_df['clv_1200s'].mean()
        
        trades_df = trades_df.sort_values('entry_timestamp_ns')
        trades_df['cum_pnl'] = trades_df['pnl_usd'].cumsum()
        trades_df['peak'] = trades_df['cum_pnl'].cummax()
        trades_df['drawdown'] = trades_df['cum_pnl'] - trades_df['peak']
        max_dd = trades_df['drawdown'].min()
        
        try:
            trades_df['edge_bucket'] = pd.cut(trades_df['edge'], bins=[0, 0.05, 0.10, 0.15, 0.20, 1.0])
            roi_by_edge = trades_df.groupby('edge_bucket', observed=False)['pnl_usd'].sum() / trades_df.groupby('edge_bucket', observed=False)['stake_usd'].sum()
        except TypeError:
            trades_df['edge_bucket'] = pd.cut(trades_df['edge'], bins=[0, 0.05, 0.10, 0.15, 0.20, 1.0])
            roi_by_edge = trades_df.groupby('edge_bucket')['pnl_usd'].sum() / trades_df.groupby('edge_bucket')['stake_usd'].sum()

        try:
            trades_df['ask_bucket'] = pd.cut(trades_df['entry_ask'], bins=[0, 0.2, 0.4, 0.6, 0.8, 1.0])
            roi_by_ask = trades_df.groupby('ask_bucket', observed=False)['pnl_usd'].sum() / trades_df.groupby('ask_bucket', observed=False)['stake_usd'].sum()
        except TypeError:
            trades_df['ask_bucket'] = pd.cut(trades_df['entry_ask'], bins=[0, 0.2, 0.4, 0.6, 0.8, 1.0])
            roi_by_ask = trades_df.groupby('ask_bucket')['pnl_usd'].sum() / trades_df.groupby('ask_bucket')['stake_usd'].sum()

        try:
            trades_df['game_time_bucket'] = pd.cut(trades_df['game_time_sec'], bins=[0, 600, 1200, 1800, 2400, 10000])
            roi_by_time = trades_df.groupby('game_time_bucket', observed=False)['pnl_usd'].sum() / trades_df.groupby('game_time_bucket', observed=False)['stake_usd'].sum()
        except TypeError:
            trades_df['game_time_bucket'] = pd.cut(trades_df['game_time_sec'], bins=[0, 600, 1200, 1800, 2400, 10000])
            roi_by_time = trades_df.groupby('game_time_bucket')['pnl_usd'].sum() / trades_df.groupby('game_time_bucket')['stake_usd'].sum()

        sorted_pnl = trades_df['pnl_usd'].sort_values(ascending=False)
        pnl_rem_1 = sorted_pnl.iloc[1:].sum() if len(sorted_pnl) > 1 else 0
        pnl_rem_3 = sorted_pnl.iloc[3:].sum() if len(sorted_pnl) > 3 else 0
        pnl_rem_5 = sorted_pnl.iloc[5:].sum() if len(sorted_pnl) > 5 else 0

        with open(md_path, "w") as f:
            f.write("# Model Value V1 Backtest Summary\n\n")
            f.write(f"- **Trades**: {num_trades}\n")
            f.write(f"- **Win Rate**: {win_rate:.2%}\n")
            f.write(f"- **Avg Ask**: {avg_ask:.3f}\n")
            f.write(f"- **Avg Edge**: {avg_edge:.3f}\n")
            f.write(f"- **Total PnL**: ${total_pnl:.2f}\n")
            f.write(f"- **Overall ROI**: {overall_roi:.2%}\n")
            f.write(f"- **Avg 900s CLV**: {avg_900s_clv:.4f}\n")
            f.write(f"- **Avg 1200s CLV**: {avg_1200s_clv:.4f}\n")
            f.write(f"- **Max Drawdown**: ${max_dd:.2f}\n\n")
            
            f.write("### ROI by Edge Bucket\n")
            f.write(roi_by_edge.to_markdown() + "\n\n")
            
            f.write("### ROI by Ask Bucket\n")
            f.write(roi_by_ask.to_markdown() + "\n\n")
            
            f.write("### ROI by Game-Time Bucket\n")
            f.write(roi_by_time.to_markdown() + "\n\n")
            
            f.write("### Profit after removing best N trades\n")
            f.write(f"- Best 1 removed: ${pnl_rem_1:.2f}\n")
            f.write(f"- Best 3 removed: ${pnl_rem_3:.2f}\n")
            f.write(f"- Best 5 removed: ${pnl_rem_5:.2f}\n")
            
    print(f"Summary written to {md_path}")

if __name__ == "__main__":
    main()
