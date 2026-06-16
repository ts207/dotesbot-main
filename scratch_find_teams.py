import pandas as pd
df = pd.read_csv('logs/raw_snapshots.csv')
for _, row in df.tail(100).iterrows():
    s = str(row['players']).lower() + str(row).lower()
    if 'kibamboni' in s or 'nande' in s:
        print(f"Match: {row['match_id']}, League: {row['league_id']}, Spectators: {row['spectators']}")
