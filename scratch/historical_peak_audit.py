import pandas as pd
import numpy as np

def run_audit():
    # Load all consolidated combat markouts
    try:
        df = pd.read_csv('logs/signal_markouts.csv')
        # Filter for combat signals only
        df = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])].copy()
    except Exception as e:
        print(f"Error: {e}")
        return

    print(f"Auditing {len(df)} historical combat signals...")

    # Calculate statistics for each available tactical horizon
    # These values represent the move in cents/delta from our entry fair price
    horizons = ['markout_3s', 'markout_10s', 'markout_30s']
    
    results = []
    for h in horizons:
        mean_val = df[h].mean()
        med_val = df[h].median()
        win_rate = (df[h] > 0).mean()
        avg_win = df[df[h] > 0][h].mean()
        avg_loss = df[df[h] < 0][h].mean()
        
        results.append({
            'Horizon': h.replace('markout_', ''),
            'Mean': round(mean_val, 5),
            'Median': round(med_val, 5),
            'WinRate': f"{win_rate:.1%}",
            'AvgWin': round(avg_win, 4),
            'AvgLoss': round(avg_loss, 4),
            'Payoff': round(abs(avg_win/avg_loss), 2) if avg_loss != 0 else 0
        })

    report = pd.DataFrame(results)
    print("\n=== HISTORICAL TACTICAL REPRICING CURVE ===")
    print(report.to_string(index=False))

    # Identify the Peak
    peak_h = report.loc[report['Mean'].idxmax()]
    print(f"\nPEAK EVENT REPRICING: {peak_h['Horizon']} horizon (Mean: {peak_h['Mean']})")

if __name__ == "__main__":
    run_audit()
