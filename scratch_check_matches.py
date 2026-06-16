import pandas as pd
import json

df = pd.read_csv('logs/raw_snapshots.csv')
last_ns = df['received_at_ns'].max()
recent_df = df[df['received_at_ns'] == last_ns]

for _, row in recent_df.iterrows():
    print(f"Match: {row['match_id']} League: {row['league_id']} Spectators: {row['spectators']}")
    players_json = row['players'].replace('false', 'False').replace('true', 'True')
    try:
        players = eval(players_json)
        names = [p.get('name', 'Unknown') for p in players]
        print(f"Players: {names[:5]} vs {names[5:]}")
    except Exception as e:
        print(e)
