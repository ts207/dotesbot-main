import pandas as pd
df = pd.read_csv('logs/raw_snapshots.csv')
last_ns = df['received_at_ns'].max()
recent = df[df['received_at_ns'] == last_ns]

for _, row in recent.iterrows():
    print(f"Match: {row['match_id']}, League: {row['league_id']}, Time: {row['game_time_sec']}, Players: {str(row['players'])[:150]}")
