import pandas as pd
from datetime import datetime, timezone, timedelta

# Load value attempts
df = pd.read_csv('logs/value_attempts.csv')
df['timestamp_utc'] = pd.to_datetime(df['timestamp_utc'])

# Filter for the last 5 hours
cutoff = datetime.now(timezone.utc) - timedelta(hours=5)
df_recent = df[df['timestamp_utc'] > cutoff]

print(f"Total attempts in last 5 hours: {len(df_recent)}")
print(f"Unique matches evaluated: {df_recent['match_id'].nunique()}")

# Count rejection reasons
print("\nRejection Reasons:")
print(df_recent['reject_reason'].value_counts())

# Group by match and find the maximum fair and edge evaluated for each match
print("\nMatch Max Values:")
match_stats = df_recent.groupby('match_id').agg(
    max_fair=('fair_price', 'max'),
    max_edge=('edge', 'max'),
    min_ask=('ask', 'min'),
    max_ask=('ask', 'max'),
    max_lead=('lead', lambda x: x.abs().max() if pd.notnull(x).any() else None)
).reset_index()
print(match_stats.to_string())

# Show distribution of edge values
print("\nEdge Quantiles:")
print(df_recent['edge'].dropna().quantile([0.5, 0.75, 0.9, 0.95, 0.99]))
