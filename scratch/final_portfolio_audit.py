import pandas as pd
import numpy as np

def run_audit():
    # 1. Combat Sniper Backtest (All Data)
    try:
        df_mark = pd.read_csv('logs/signal_markouts.csv')
        shadow = pd.read_csv('logs/shadow_trades.csv')
        df_sig = pd.read_csv('logs/signals.csv')
        
        # combat only
        combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
        
        # Get Steam Age from signals, join with markouts
        df_sig = df_sig.rename(columns={'timestamp_utc': 'signal_ts'})
        df_mark = df_mark.rename(columns={'signal_timestamp_utc': 'signal_ts'})
        
        # Use match_id and ts_round for joining across multiple logs
        df_sig['ts_round'] = pd.to_datetime(df_sig['signal_ts']).dt.round('S')
        df_mark['ts_round'] = pd.to_datetime(df_mark['signal_ts']).dt.round('S')
        
        merged = pd.merge(df_sig[['ts_round', 'match_id', 'steam_age_ms']], 
                         df_mark[['ts_round', 'match_id', 'markout_30s', 'event_type', 'reference_ask']], 
                         on=['ts_round', 'match_id'], how='inner')
        
        # FILTER: 15s Gate + 2 Events + 90c Cap
        portfolio_sigs = merged[
            (merged['event_type'].isin(combat_types)) & 
            (merged['steam_age_ms'] <= 15000) &
            (merged['reference_ask'] <= 0.90)
        ].copy()

        print("=== PORTFOLIO COMPONENT 1: DOTA COMBAT SNIPER (15s Gate) ===")
        print(f"Historical Sample Size: n={len(portfolio_sigs)}")
        if not portfolio_sigs.empty:
            wr = (portfolio_sigs['markout_30s'] > 0).mean()
            mean_m = portfolio_sigs['markout_30s'].mean()
            print(f"Win Rate (30s): {wr:.1%}")
            print(f"Avg Markout (30s): {mean_m:+.4f}")
            print(f"Proj PnL ($100 stake/sig): ${len(portfolio_sigs) * mean_m * 100:.2f}")

        # 2. Dota Scalp Audit
        try:
            df_scalp = pd.read_csv('logs/scalp_trades.csv')
            print("\n=== PORTFOLIO COMPONENT 2: DOTA BUY-BOTH SCALP ===")
            print(f"Dota Pairs: {len(df_scalp)}")
            print(f"Total Scalp PnL: ${df_scalp['pnl_usd'].sum():+.2f}")
            print(f"Scalp Win Rate: {(df_scalp['pnl_usd'] > 0).mean():.1%}")
        except: print("\nNo Dota scalp data found.")

        # 3. LoL Scalp Audit (Safe Mode)
        try:
            df_lol = pd.read_csv('logs/lol_scalp_paper.csv')
            print("\n=== PORTFOLIO COMPONENT 3: LoL SCALP (SAFE MODE) ===")
            print(f"LoL Pairs: {len(df_lol)}")
            print(f"Total LoL PnL: ${df_lol['total_pnl_usd'].sum():+.2f}")
        except: print("\nNo LoL scalp data found.")

    except Exception as e:
        print(f"Audit error: {e}")

if __name__ == "__main__":
    run_audit()
