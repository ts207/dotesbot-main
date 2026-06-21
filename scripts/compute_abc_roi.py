import pandas as pd
import numpy as np

print("Loading trades...")
trades_df = pd.read_csv("reports/abc_roi/model_value_v1_trades.csv")
replay_df = pd.read_parquet("data_v2/model_value_replay.parquet")

valid_matches = replay_df.groupby('dota_match_id')['settled_yes_outcome'].last().notna()
resolved_dota_ids = set(valid_matches[valid_matches].index)
# Add match_id mapping from dota_match_id
match_id_mapping = replay_df[replay_df['dota_match_id'].isin(resolved_dota_ids)]['match_id'].unique()
resolved_matches = set(match_id_mapping)

trades_df['match_id'] = trades_df['match_id'].astype(str)
# Calculate true settlement PnL for resolved trades
true_pnl = []
for _, trade in resolved_trades.iterrows():
    token_id = str(trade['token_id'])
    future_rows = replay_df[(replay_df['timestamp_ns'] > trade['entry_timestamp_ns'])]
    
    yes_match = future_rows[future_rows['yes_token_id'].astype(str) == token_id]
    no_match = future_rows[future_rows['no_token_id'].astype(str) == token_id]
    
    if not yes_match.empty and yes_match.iloc[-1].get('settled_yes_outcome') == 1:
        true_pnl.append(1.0 - trade['entry_ask'])
    elif not no_match.empty and no_match.iloc[-1].get('settled_yes_outcome') == 0:
        true_pnl.append(1.0 - trade['entry_ask'])
    else:
        true_pnl.append(-trade['entry_ask'])

resolved_pnl_sum = sum(true_pnl)
resolved_roi = resolved_pnl_sum / resolved_trades['entry_ask'].sum() if len(resolved_trades) > 0 and resolved_trades['entry_ask'].sum() > 0 else 0.0

# B: Unresolved marked at latest available mid
b_pnl = resolved_pnl_sum + trades_df[~trades_df['is_resolved']]['last_mid_clv'].fillna(0).sum()
b_roi = b_pnl / trades_df['entry_ask'].sum() if len(trades_df) > 0 and trades_df['entry_ask'].sum() > 0 else 0.0

# C: Unresolved counted pessimistically as losses
trades_df['pessimistic_clv'] = -trades_df['entry_ask']
c_pnl = resolved_pnl_sum + trades_df[~trades_df['is_resolved']]['pessimistic_clv'].sum()
c_roi = c_pnl / trades_df['entry_ask'].sum() if len(trades_df) > 0 and trades_df['entry_ask'].sum() > 0 else 0.0

print(f"Total Trades: {len(trades_df)}")
print(f"Resolved Trades: {len(resolved_trades)}")
print(f"Unresolved Trades: {len(trades_df) - len(resolved_trades)}")
print()
print(f"A. Resolved-only ROI: {resolved_roi:.2%}")
print(f"B. Unresolved marked at latest available mid: {b_roi:.2%}")
print(f"C. Unresolved counted pessimistically as losses: {c_roi:.2%}")
