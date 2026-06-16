import pandas as pd
import numpy as np

df = pd.read_csv("logs/raw_snapshots.csv")
df['received_at_ns'] = pd.to_numeric(df['received_at_ns'], errors='coerce')
df = df.dropna(subset=['received_at_ns', 'match_id'])

print(f"Total snapshots: {len(df)}")

# Sort by match and time
df = df.sort_values(['match_id', 'received_at_ns'])

# Calculate the gap between successive receipts for each match
df['arrival_gap_sec'] = df.groupby('match_id')['received_at_ns'].diff() / 1e9

# Calculate the jump in game_time for each match
df['gt_jump_sec'] = df.groupby('match_id')['game_time_sec'].diff()

print("\n--- Arrival Gap (Real Time) ---")
print(df['arrival_gap_sec'].describe())

print("\n--- Game Time Jump (Simulation Time) ---")
print(df['gt_jump_sec'].describe())

print("\n--- source_update_age_sec (Staleness at Receipt) ---")
print(df['source_update_age_sec'].describe())

# Check how often we get "Burst" updates vs "Long silence"
print("\nGap Distribution:")
print(pd.cut(df['arrival_gap_sec'], bins=[0, 1, 5, 10, 20, 60, 120]).value_counts().sort_index())

