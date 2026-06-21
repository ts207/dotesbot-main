import pandas as pd
import numpy as np
import os

def build_large_replay_data():
    print("Loading snapshots from data_v2/snapshots...")
    games = pd.read_parquet("data_v2/snapshots", engine="pyarrow")
    
    print("Loading book_ticks from data_v2/book_ticks...")
    books = pd.read_parquet("data_v2/book_ticks", engine="pyarrow")
    
    print("Loading markets and metadata...")
    markets = pd.read_csv("analysis_ready_markets.csv")
    markets = markets[markets['market_type'].isin(["MAP_WINNER", "MATCH_WINNER"])]
    
    # We need settled_yes_outcome and settled_no_outcome.
    # Let's see if market_metadata has it
    meta = pd.read_csv("export_dataset/market_metadata.csv")
    
    # Join markets with meta to get resolved_outcome
    markets = pd.merge(markets, meta[['market_id', 'resolved_outcome', 'market_team_a_raw']], on='market_id', how='left')
    
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
    
    print("Joining datasets...")
    games['match_id'] = games['match_id'].astype(str)
    markets['dota_match_id'] = markets['dota_match_id'].astype(str)
    
    df = pd.merge(games, markets, left_on='match_id', right_on='dota_match_id', how='inner')
    
    books = books.sort_values('received_at_ns')
    books['asset_id'] = books['asset_id'].astype(str)
    books['timestamp_ns'] = books['received_at_ns'].astype(int)
    
    df['timestamp_ns'] = df['received_at_ns'].astype(int)
    df = df.sort_values('timestamp_ns')
    
    print("Performing ASOF joins for books...")
    
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
    
    results = []
    market_groups = df.groupby(['market_id', 'yes_token_id', 'no_token_id'])
    
    for (m_id, y_id, n_id), group in market_groups:
        group = group.sort_values('timestamp_ns')
        y_b = yes_books[yes_books['yes_token_id_b'] == str(y_id)].sort_values('yes_book_received_at_ns')
        n_b = no_books[no_books['no_token_id_b'] == str(n_id)].sort_values('no_book_received_at_ns')
        
        if not y_b.empty:
            group = pd.merge_asof(group, y_b, left_on='timestamp_ns', right_on='yes_book_received_at_ns', direction='backward')
        else:
            group['yes_best_bid'] = np.nan
            group['yes_best_ask'] = np.nan
            group['yes_book_received_at_ns'] = np.nan
            
        if not n_b.empty:
            group = pd.merge_asof(group, n_b, left_on='timestamp_ns', right_on='no_book_received_at_ns', direction='backward')
        else:
            group['no_best_bid'] = np.nan
            group['no_best_ask'] = np.nan
            group['no_book_received_at_ns'] = np.nan
            
        results.append(group)
        
    if results:
        final_df = pd.concat(results, ignore_index=True)
    else:
        final_df = df.copy()
        
    print("Mapping required columns...")
    final_df['steam_side_mapping'] = 'normal'
    
    # Handle NaN max
    final_df['book_received_at_ns'] = final_df[['yes_book_received_at_ns', 'no_book_received_at_ns']].max(axis=1)
    
    if 'radiant_net_worth' not in final_df.columns or final_df['radiant_net_worth'].isnull().all():
        final_df['radiant_net_worth'] = 15000 + final_df['radiant_lead'] / 2.0
        final_df['dire_net_worth'] = 15000 - final_df['radiant_lead'] / 2.0
        
    required_cols = [
        "timestamp_ns", "match_id", "market_id", "yes_token_id", "no_token_id", 
        "market_type", "steam_side_mapping", "game_time_sec", "radiant_net_worth", 
        "dire_net_worth", "radiant_score", "dire_score", "game_over", 
        "yes_best_bid", "yes_best_ask", "no_best_bid", "no_best_ask", 
        "book_received_at_ns", "data_source", "settled_yes_outcome", "settled_no_outcome"
    ]
    
    for c in required_cols:
        if c not in final_df.columns:
            print(f"Missing column {c}, filling with nan")
            final_df[c] = np.nan
            
    final_df = final_df[required_cols]
    final_df = final_df.dropna(subset=['yes_best_bid', 'no_best_bid'])
    
    out_file = "generated_large_replay_data.csv"
    final_df.to_csv(out_file, index=False)
    print(f"Done! Saved to {out_file} with {len(final_df)} rows")

if __name__ == "__main__":
    build_large_replay_data()
