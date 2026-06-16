import pandas as pd
df = pd.read_csv('logs/raw_snapshots.csv')
ended = df[df['game_over'] == True]
if not ended.empty:
    for _, row in ended.tail(5).iterrows():
        print(f"Match: {row['match_id']}, League: {row['league_id']}, Players: {str(row['players'])[:100]}")
else:
    print('No games marked game_over=True recently.')

ended2 = df[df['game_time_sec'] > 2000]
print("Long games:")
for match_id in ended2['match_id'].unique():
    r = ended2[ended2['match_id'] == match_id].iloc[-1]
    print(f"Match: {match_id}, League: {r['league_id']}, Time: {r['game_time_sec']}, Players: {str(r['players'])[:100]}")
