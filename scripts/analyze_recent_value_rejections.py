import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
from dotenv import load_dotenv

# Load env variables to get exact thresholds
load_dotenv()
VALUE_MIN_EDGE = float(os.getenv("VALUE_MIN_EDGE", "0.15"))
VALUE_MIN_FAIR = float(os.getenv("VALUE_MIN_FAIR", "0.70"))
VALUE_MAX_PRICE = float(os.getenv("VALUE_MAX_PRICE", "0.84"))
VALUE_MIN_PRICE = float(os.getenv("VALUE_MIN_PRICE", "0.55"))

def main():
    log_path = Path("logs/value_attempts.csv")
    if not log_path.exists():
        print(f"Log file not found: {log_path}")
        return

    df = pd.read_csv(log_path)
    if df.empty:
        print("No attempts to analyze.")
        return

    df['timestamp_utc'] = pd.to_datetime(df['timestamp_utc'])
    
    # Calculate distances
    df['edge_shortfall'] = np.where(df['edge'].notna(), VALUE_MIN_EDGE - df['edge'], np.nan)
    df['fair_shortfall'] = np.where(df['fair_price'].notna(), VALUE_MIN_FAIR - df['fair_price'], np.nan)
    df['price_ceiling_excess'] = np.where(df['ask'].notna(), df['ask'] - VALUE_MAX_PRICE, np.nan)
    df['price_floor_shortfall'] = np.where(df['ask'].notna(), VALUE_MIN_PRICE - df['ask'], np.nan)

    # Split price_too_low
    # Criteria based on market disagreement alpha logic
    # Candidate: ask < 0.55, edge > 0.30, leader
    is_price_too_low = df['reject_reason'] == 'price_too_low'
    
    df.loc[is_price_too_low & (df['edge'] > 0.30) & (df['fair_price'] >= 0.50), 'reject_reason'] = 'price_too_low: deep_discount_high_edge_leader_candidate'
    df.loc[is_price_too_low & (df['edge'] <= 0.30) & (df['fair_price'] >= 0.50), 'reject_reason'] = 'price_too_low: cheap_leader_toxic'
    df.loc[is_price_too_low & (df['fair_price'] < 0.50), 'reject_reason'] = 'price_too_low: cheap_reject_broad'
    df.loc[is_price_too_low & df['fair_price'].isna(), 'reject_reason'] = 'price_too_low: cheap_reject_broad'

    # Bucket edge_too_small
    is_edge_too_small = df['reject_reason'] == 'edge_too_small'
    bins = [-np.inf, 0.005, 0.010, 0.025, 0.050, np.inf]
    labels = ['0.000 - 0.005 below threshold', '0.005 - 0.010 below threshold', 
              '0.010 - 0.025 below threshold', '0.025 - 0.050 below threshold', '>0.050 below threshold']
    
    df.loc[is_edge_too_small, 'edge_bucket'] = pd.cut(df.loc[is_edge_too_small, 'edge_shortfall'], bins=bins, labels=labels)

    # Calculate match-level max edge and closest miss
    match_stats = df.groupby('match_id').agg(
        max_edge=('edge', 'max'),
    ).reset_index()
    
    # Calculate closest miss per match (only for edge_too_small)
    closest_misses = df[is_edge_too_small].groupby('match_id')['edge_shortfall'].min().reset_index()
    closest_misses.rename(columns={'edge_shortfall': 'closest_edge_miss_per_match'}, inplace=True)
    
    match_stats = match_stats.merge(closest_misses, on='match_id', how='left')

    # Calculate seconds until price > 0.84 after a near miss
    df['seconds_until_price_over_0_84_after_near_miss'] = np.nan
    near_misses = df[is_edge_too_small & (df['edge_shortfall'] <= 0.010)]
    
    for idx, row in near_misses.iterrows():
        match_id = row['match_id']
        ts = row['timestamp_utc']
        future_rows = df[(df['match_id'] == match_id) & (df['timestamp_utc'] > ts) & (df['ask'] > 0.84)]
        if not future_rows.empty:
            first_over = future_rows.iloc[0]
            delta_sec = (first_over['timestamp_utc'] - ts).total_seconds()
            df.at[idx, 'seconds_until_price_over_0_84_after_near_miss'] = delta_sec

    # Generate Report
    print("="*60)
    print(" VALUE ENGINE REJECTION ANALYSIS ")
    print("="*60)
    print(f"Total Evaluations: {len(df)}")
    print(f"Unique Matches: {df['match_id'].nunique()}")
    
    print("\n--- Rejection Reason Distribution ---")
    print(df['reject_reason'].value_counts().to_string())

    print("\n--- Edge Too Small Bucketing ---")
    if is_edge_too_small.any():
        print(df.loc[is_edge_too_small, 'edge_bucket'].value_counts().sort_index().to_string())
    else:
        print("No 'edge_too_small' rejections found.")

    print("\n--- Match-Level Edge Stats ---")
    print(match_stats.to_string(index=False))

    print("\n--- Near Miss Price Corrections ---")
    near_miss_with_correction = df[df['seconds_until_price_over_0_84_after_near_miss'].notna()]
    if not near_miss_with_correction.empty:
        print(near_miss_with_correction[['timestamp_utc', 'match_id', 'edge', 'ask', 'edge_shortfall', 'seconds_until_price_over_0_84_after_near_miss']].to_string(index=False))
    else:
        print("No near misses corrected above VALUE_MAX_PRICE within tracked window.")
        
if __name__ == '__main__':
    main()
