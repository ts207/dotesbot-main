import pandas as pd
df = pd.read_csv('logs/raw_snapshots.csv')
matches = df[df['players'].str.contains('171097887')]
print(len(matches))
if len(matches) > 0:
    row = matches.iloc[-1]
    print(f'Match: {row["match_id"]}, League: {row["league_id"]}, Time: {row["game_time_sec"]}')
    print('Players:', row["players"])
