import pandas as pd
import numpy as np

def load_trades(path):
    df = pd.read_csv(path)
    # create a unique id for the trade: match_id + token_id + side
    df['trade_key'] = df['match_id'].astype(str) + "_" + df['token_id'].astype(str) + "_" + df['side'].astype(str)
    return df

def main():
    t01 = load_trades("reports/model_value_audit_20260621_195300/robustness_th_0.01/model_value_v1_trades.csv")
    t02 = load_trades("reports/model_value_audit_20260621_195300/robustness_th_0.02/model_value_v1_trades.csv")
    
    keys_01 = set(t01['trade_key'])
    keys_02 = set(t02['trade_key'])
    
    common = keys_01.intersection(keys_02)
    only_01 = keys_01 - keys_02
    only_02 = keys_02 - keys_01
    
    print(f"Trades common to both thresholds: {len(common)}")
    print(f"Trades only in 0.01: {len(only_01)}")
    print(f"Trades only in 0.02: {len(only_02)}")
    print()
    
    if len(common) > 0:
        c01 = t01[t01['trade_key'].isin(common)].set_index('trade_key').sort_index()
        c02 = t02[t02['trade_key'].isin(common)].set_index('trade_key').sort_index()
        
        # Calculate differences
        # Timestamps are in nanoseconds, convert to seconds
        entry_time_diff_sec = (c01['entry_timestamp_ns'] - c02['entry_timestamp_ns']) / 1e9
        ask_diff = c01['entry_ask'] - c02['entry_ask']
        p_model_diff = c01['model_probability'] - c02['model_probability']
        edge_diff = c01['edge'] - c02['edge']
        
        pnl_diff = c01['pnl_usd'] - c02['pnl_usd']
        
        # Use clv_1200s for CLV diff
        clv_diff = c01['clv_1200s'] - c02['clv_1200s']
        
        avg_earlier = -entry_time_diff_sec.mean() # Positive if 0.01 is earlier
        avg_ask_improv = -ask_diff.mean() # Positive if 0.01 ask is lower
        
        print(f"avg earlier_entry_seconds for common trades: {avg_earlier:.2f} seconds")
        print(f"avg ask_improvement for common trades: {avg_ask_improv:.4f}")
        print(f"PnL delta from ask improvement: ${pnl_diff.sum():.2f}")
        print(f"Avg PnL delta per trade: ${pnl_diff.mean():.4f}")
        print(f"CLV 1200s delta (0.01 - 0.02 avg): {clv_diff.mean():.4f}")
        
        # Details
        print("\n--- Common Trades Breakdown ---")
        df_diff = pd.DataFrame({
            '0.01_entry_time': c01['entry_timestamp_ns'],
            '0.02_entry_time': c02['entry_timestamp_ns'],
            'time_diff_sec': entry_time_diff_sec,
            '0.01_ask': c01['entry_ask'],
            '0.02_ask': c02['entry_ask'],
            'ask_diff': ask_diff,
            'pnl_diff': pnl_diff,
            '0.01_pnl': c01['pnl_usd'],
            '0.02_pnl': c02['pnl_usd']
        })
        pd.set_option('display.max_rows', None)
        print(df_diff[['time_diff_sec', '0.01_ask', '0.02_ask', 'ask_diff', '0.01_pnl', '0.02_pnl', 'pnl_diff']])
        
    if len(only_01) > 0:
        print("\n--- Trades only in 0.01 ---")
        print(t01[t01['trade_key'].isin(only_01)][['trade_key', 'entry_ask', 'pnl_usd', 'clv_1200s', 'settlement_outcome']])

    if len(only_02) > 0:
        print("\n--- Trades only in 0.02 ---")
        print(t02[t02['trade_key'].isin(only_02)][['trade_key', 'entry_ask', 'pnl_usd', 'clv_1200s', 'settlement_outcome']])

if __name__ == "__main__":
    main()
