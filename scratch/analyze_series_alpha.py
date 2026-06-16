import pandas as pd
import numpy as np

try:
    df = pd.read_csv("logs/book_moves.csv")
except Exception as e:
    print(f"Error loading logs: {e}")
    exit()

df_map = df[df['market_name'].str.contains('Game')].copy()
df_series = df[~df['market_name'].str.contains('Game')].copy()

print(f"Total Map Moves: {len(df_map)}")
print(f"Total Series Moves: {len(df_series)}")

