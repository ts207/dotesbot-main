import pandas as pd
import numpy as np

def analyze():
    try:
        df = pd.read_csv('logs/signal_markouts.csv')
        combat = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])]
        
        print(f"Total Combat Signals: {len(combat)}")
        
        # Split by price bucket
        low = combat[combat['reference_ask'] <= 0.82]
        high = combat[combat['reference_ask'] > 0.82]
        
        print("\n--- Performance by Price Bucket (30s) ---")
        for label, subset in [("Price <= 0.82", low), ("Price > 0.82", high)]:
            win_r = (subset['markout_30s'] > 0).mean()
            mean_m = subset['markout_30s'].mean()
            print(f"{label:15}: n={len(subset):<3} | Win%={win_r:.1%} | Mean={mean_m:+.4f}")

        # Impact of raising cap to 0.90
        # Signals captured in the 0.82 to 0.90 window
        captured = combat[(combat['reference_ask'] > 0.82) & (combat['reference_ask'] <= 0.90)]
        print(f"\nCaptured by raising cap to 0.90: n={len(captured)}")
        if not captured.empty:
            win_r = (captured['markout_30s'] > 0).mean()
            mean_m = captured['markout_30s'].mean()
            print(f"Captured Window Markout: {mean_m:+.4f} (Win%={win_r:.1%})")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    analyze()
