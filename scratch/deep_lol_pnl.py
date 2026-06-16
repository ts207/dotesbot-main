import pandas as pd
import numpy as np

def analyze():
    try:
        df = pd.read_csv('logs/lol_scalp_paper.csv')
    except:
        print("No LoL scalp file found.")
        return

    if df.empty:
        print('No LoL scalp data found.')
        return

    print('=== LoL Scalp PnL Distribution ===')
    print(df['total_pnl_usd'].describe())
    
    print('\n=== LoL Scalp by Close Reason ===')
    print(df.groupby('close_reason')['total_pnl_usd'].agg(['count', 'mean', 'sum']))

    # Analyze large losses
    losses = df[df['total_pnl_usd'] < -5]
    if not losses.empty:
        print('\n=== Deep Dive: Large Losses (>$5) ===')
        for _, row in losses.iterrows():
            print(f"{row['question']}: ${row['total_pnl_usd']:.2f}")
            print(f"  Duration: {row['duration_sec']:.0f}s, Reason: {row['close_reason']}")
            print(f"  Entries:   YES={row['yes_entry_px']} NO={row['no_entry_px']}")
            print(f"  Scratches: YES={row['yes_scratched_px']} NO={row['no_scratched_px']}")

if __name__ == "__main__":
    analyze()
