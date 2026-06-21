import os
import pandas as pd
import numpy as np
from mapping import load_valid_mappings

import yaml
def build_model_value_replay():
    print("Loading valid mappings...")
    import yaml
    with open("markets.yaml", "r") as f:
        y = yaml.safe_load(f)
        markets = pd.DataFrame(y['markets'])
    
    if os.path.exists("logs/runtime_markets.yaml"):
        with open("logs/runtime_markets.yaml", "r") as f:
            y_r = yaml.safe_load(f)
            if y_r and 'markets' in y_r:
                r_df = pd.DataFrame(y_r['markets'])
                # Simple append for backtest coverage, ignoring overrides for simplicity
                markets = pd.concat([markets, r_df], ignore_index=True)
        
    print(f"Loaded {len(markets)} valid markets.")
    
    # Filter for MAP_WINNER and MATCH_WINNER
    markets = markets[markets['market_type'].isin(["MAP_WINNER", "MATCH_WINNER"])]
    
    # Ensure dota_match_id is string
    markets['dota_match_id'] = markets['dota_match_id'].astype(str)
    
    print("Loading metadata...")
    meta = pd.read_csv("export_dataset/market_metadata.csv")
    meta['market_id'] = meta['market_id'].astype(str)
    markets['market_id'] = markets['market_id'].astype(str)
    
    # Join markets with meta to get resolved_outcome
    markets = pd.merge(markets, meta[['market_id', 'resolved_outcome', 'market_team_a_raw', 'market_team_b_raw']], on='market_id', how='left')
    
    # Define outcomes
    def get_yes_outcome(row):
        r = row['resolved_outcome']
        a = row['market_team_a_raw']
        if pd.isna(r) or pd.isna(a): return np.nan
        return "WIN" if str(r).strip() == str(a).strip() else "LOSS"
        
    def get_no_outcome(row):
        y = get_yes_outcome(row)
        if pd.isna(y): return np.nan
        return "LOSS" if y == "WIN" else "WIN"
        
    markets['settled_yes_outcome'] = markets.apply(get_yes_outcome, axis=1)
    markets['settled_no_outcome'] = markets.apply(get_no_outcome, axis=1)
    
    print("Loading snapshots...")
    games = pd.read_parquet("data_v2/snapshots")
    games['match_id'] = games['match_id'].astype(str)
    
    print("Joining games with markets...")
    df = pd.merge(games, markets, left_on='match_id', right_on='dota_match_id', how='inner')
    
    print(f"After join, we have {len(df)} game-market snapshot combinations.")
    
    print("Loading books...")
    books = pd.read_parquet("data_v2/book_ticks")
    books['timestamp_ns'] = books['received_at_ns']
    books = books.sort_values('timestamp_ns')
    books['asset_id'] = books['asset_id'].astype(str)
    
    df = df.sort_values('received_at_ns')
    
    print("Performing ASOF joins...")
    yes_books = books.rename(columns={
        'asset_id': 'yes_token_id_b',
        'best_bid': 'yes_best_bid',
        'best_ask': 'yes_best_ask',
        'timestamp_ns': 'yes_book_received_at_ns'
    })[['yes_token_id_b', 'yes_best_bid', 'yes_best_ask', 'yes_book_received_at_ns']]
    
    no_books = books.rename(columns={
        'asset_id': 'no_token_id_b',
        'best_bid': 'no_best_bid',
        'best_ask': 'no_best_ask',
        'timestamp_ns': 'no_book_received_at_ns'
    })[['no_token_id_b', 'no_best_bid', 'no_best_ask', 'no_book_received_at_ns']]
    
    df['yes_token_id'] = df['yes_token_id'].astype(str)
    df['no_token_id'] = df['no_token_id'].astype(str)
    
    df = pd.merge_asof(
        df,
        yes_books,
        left_on='received_at_ns',
        right_on='yes_book_received_at_ns',
        left_by='yes_token_id',
        right_by='yes_token_id_b',
        direction='backward'
    )
    
    df = pd.merge_asof(
        df,
        no_books,
        left_on='received_at_ns',
        right_on='no_book_received_at_ns',
        left_by='no_token_id',
        right_by='no_token_id_b',
        direction='backward'
    )
    
    # Map backtest expected columns
    df['timestamp_ns'] = df['received_at_ns']
    df['data_source'] = df.get('data_source', 'top_live')
    
    # For model value train, we need token-level rows
    # so we melt the dataframe
    print("Unpivoting to token-level rows...")
    yes_df = df.copy()
    yes_df["token_id"] = yes_df["yes_token_id"]
    yes_df["best_bid"] = yes_df["yes_best_bid"]
    yes_df["best_ask"] = yes_df["yes_best_ask"]
    yes_df["settlement_outcome"] = yes_df["settled_yes_outcome"]
    
    # Apply token_net_worth_lead logic: assuming normal mapping where team_a is Radiant
    # if mapping has steam_side_mapping, we can use it
    # 'steam_side_mapping': 'normal' means radiant=yes, dire=no
    yes_df["token_net_worth_lead"] = np.where(
        yes_df['steam_side_mapping'] == 'normal',
        yes_df.get("radiant_net_worth", 0) - yes_df.get("dire_net_worth", 0),
        yes_df.get("dire_net_worth", 0) - yes_df.get("radiant_net_worth", 0)
    )
    yes_df["token_score_margin"] = np.where(
        yes_df['steam_side_mapping'] == 'normal',
        yes_df.get("radiant_score", 0) - yes_df.get("dire_score", 0),
        yes_df.get("dire_score", 0) - yes_df.get("radiant_score", 0)
    )
    yes_df["book_received_at_ns"] = yes_df.get("yes_book_received_at_ns", 0)
    
    no_df = df.copy()
    no_df["token_id"] = no_df["no_token_id"]
    no_df["best_bid"] = no_df["no_best_bid"]
    no_df["best_ask"] = no_df["no_best_ask"]
    no_df["settlement_outcome"] = no_df["settled_no_outcome"]
    
    no_df["token_net_worth_lead"] = np.where(
        no_df['steam_side_mapping'] == 'normal',
        no_df.get("dire_net_worth", 0) - no_df.get("radiant_net_worth", 0),
        no_df.get("radiant_net_worth", 0) - no_df.get("dire_net_worth", 0)
    )
    no_df["token_score_margin"] = np.where(
        no_df['steam_side_mapping'] == 'normal',
        no_df.get("dire_score", 0) - no_df.get("radiant_score", 0),
        no_df.get("radiant_score", 0) - no_df.get("dire_score", 0)
    )
    no_df["book_received_at_ns"] = no_df.get("no_book_received_at_ns", 0)
    
    final_df = pd.concat([yes_df, no_df], ignore_index=True)
    
    # PyArrow hates mixed types. Convert object columns to string
    for col in final_df.select_dtypes(include=['object']).columns:
        final_df[col] = final_df[col].astype(str)
        
    print(f"Writing {len(final_df)} rows to data_v2/model_value_replay.parquet...")
    final_df.to_parquet("data_v2/model_value_replay.parquet", index=False)
    print("Done!")

if __name__ == '__main__':
    build_model_value_replay()
