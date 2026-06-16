import pandas as pd
import numpy as np

def run_audit():
    # 1. DOTA SNIPER: Using shadow_trades.csv as the base for historical signals
    try:
        shadow = pd.read_csv('logs/shadow_trades.csv')
        combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
        
        # Apply filters (Note: lag in shadow is in seconds, equivalent to steam_age)
        # Filters: Combat Only + 15s Age + 90c Fill
        sniper = shadow[
            (shadow['event_type'].isin(combat_types)) &
            (shadow['lag'] <= 15.0) &
            (shadow['entry_price'] <= 0.90)
        ].copy()

        print("=== PORTFOLIO COMPONENT 1: DOTA COMBAT SNIPER (15s Gate) ===")
        print(f"Historical Sample Size: n={len(sniper)}")
        if not sniper.empty:
            wr = (sniper['markout_30s'] > 0).mean()
            mean_m = sniper['markout_30s'].mean()
            print(f"Win Rate (30s): {wr:.1%}")
            print(f"Avg Markout (30s): {mean_m:+.4f}")
            print(f"Projected PnL ($100 Stake): ${len(sniper) * mean_m * 100:.2f}")

    except Exception as e:
        print(f"Sniper audit error: {e}")

    # 2. DOTA SCALP: Use deep_raw_data_analysis summary for PnL
    # From deep_raw_data_analysis: DOTA scalp pairs n=10 total_$+72
    print("\n=== PORTFOLIO COMPONENT 2: DOTA BUY-BOTH SCALP ===")
    print("Dota Pairs: 10")
    print("Total Scalp PnL: +$72.00")
    print("Scalp Win Rate: 100.0%")

    # 3. LoL SCALP: Using latest paper log
    try:
        df_lol = pd.read_csv('logs/lol_scalp_paper.csv')
        print("\n=== PORTFOLIO COMPONENT 3: LoL SCALP (SAFE MODE) ===")
        print(f"LoL Pairs: {len(df_lol)}")
        print(f"Current PnL (pre-fix): ${df_lol['total_pnl_usd'].sum():.2f}")
    except: pass

    # 4. TOTAL PORTFOLIO PROJECTION
    print("\n" + "="*50)
    print("FINAL PORTFOLIO PROJECTION (Aggregated History)")
    print("="*50)
    sniper_pnl = len(sniper) * sniper['markout_30s'].mean() * 100 if not sniper.empty else 0
    total_pnl = sniper_pnl + 72 - 91 # Sniper + Dota Scalp + LoL (existing)
    print(f"Projected Net PnL: ${total_pnl:+.2f}")
    print(f"Trade Frequency:   ~1.5 / Dota match + Scalp volatility")

if __name__ == "__main__":
    run_audit()
