import pandas as pd
import numpy as np

def analyze():
    # 1. Load and Isolate BLAST Slam data
    try:
        df = pd.read_csv('logs/signal_markouts.csv')
        # Isolation by name pattern
        blast = df[df['market_name'].str.contains('BLAST Slam', na=False, case=False)].copy()
        
        if blast.empty:
            print("No BLAST Slam data found in signal_markouts.csv.")
            return

        print(f"Analyzing {len(blast)} BLAST Slam signals...")

        # 2. Event Distribution
        print("\n--- Event Distribution ---")
        print(blast['event_type'].value_counts())

        # 3. Tactical Repricing Curve
        horizons = ['markout_3s', 'markout_10s', 'markout_30s']
        print("\n--- Tactical Repricing Curve (Mean Markout) ---")
        for h in horizons:
            mean_m = blast[h].mean()
            win_r = (blast[h] > 0).mean()
            print(f"{h:12}: {mean_m:+.4f} (Win%: {win_r:.1%})")

        # 4. Deep Dive by Event Type
        print("\n--- Alpha by Event Type (30s) ---")
        type_perf = blast.groupby('event_type')['markout_30s'].agg(['count', 'mean', 'median'])
        print(type_perf.sort_values('mean', ascending=False))

        # 5. Latency Check (if data available in signals.csv)
        try:
            sigs = pd.read_csv('logs/signals.csv')
            blast_sigs = sigs[sigs['market_name'].str.contains('BLAST Slam', na=False, case=False)]
            if not blast_sigs.empty:
                print("\n--- Steam Age at Signal Time ---")
                print(blast_sigs['steam_age_ms'].describe(percentiles=[.5, .75, .9]))
        except: pass

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    analyze()
