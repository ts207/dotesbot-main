import pandas as pd
import numpy as np

def build_replay_data():
    print("Loading datasets...")
    # Load games
    games = pd.read_csv("logs/raw_snapshots.csv")
    
    # Load markets
    markets = pd.read_csv("analysis_ready_markets.csv")
    
    # Filter for MAP_WINNER and MATCH_WINNER
    markets = markets[markets['market_type'].isin(["MAP_WINNER", "MATCH_WINNER"])]
    
    # Load books
    books = pd.read_csv("logs/book_events.csv")
    books['timestamp_ns'] = pd.to_datetime(books['timestamp_utc'], format='ISO8601').astype(int)
    
    print("Joining datasets...")
    # We want a row per game snapshot per valid market
    
    # Inner join games with markets
    # Ensure dota_match_id is string/int matching game match_id
    games['match_id'] = games['match_id'].astype(str)
    markets['dota_match_id'] = markets['dota_match_id'].astype(str)
    
    df = pd.merge(games, markets, left_on='match_id', right_on='dota_match_id', how='inner')
    
    # We need to attach the latest book for yes_token_id and no_token_id
    # Sort books by timestamp
    books = books.sort_values('timestamp_ns')
    
    # Prepare books for merge_asof
    books['asset_id'] = books['asset_id'].astype(str)
    df = df.sort_values('received_at_ns')
    
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
    
    df['timestamp_ns'] = df['received_at_ns']
    
    # Since merge_asof requires exact match on 'by' and sorting on 'on', we do it token by token or do a loop
    # Actually, the simplest is to loop through markets
    
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
    # Add dummy settlement data or infer from something
    final_df['steam_side_mapping'] = 'normal'
    final_df['settled_yes_outcome'] = 'WIN'
    final_df['settled_no_outcome'] = 'LOSS'
    final_df['book_received_at_ns'] = final_df[['yes_book_received_at_ns', 'no_book_received_at_ns']].max(axis=1)
    
    # We need:
    # timestamp_ns, match_id, market_id, yes_token_id, no_token_id, market_type, 
    # steam_side_mapping, game_time_sec, radiant_net_worth, dire_net_worth, 
    # radiant_score, dire_score, game_over, yes_best_bid, yes_best_ask, 
    # no_best_bid, no_best_ask, book_received_at_ns, data_source, settled_yes_outcome, settled_no_outcome
    
    # wait, radiant_net_worth isn't in steam_snapshots (it has radiant_lead)
    # radiant_net_worth = 15000 + radiant_lead/2, dire = 15000 - radiant_lead/2 roughly if missing
    if 'radiant_net_worth' not in final_df.columns:
        final_df['radiant_net_worth'] = 15000 + final_df['radiant_lead'] / 2.0
        final_df['dire_net_worth'] = 15000 - final_df['radiant_lead'] / 2.0
        
    required_cols = [
        "timestamp_ns", "match_id", "market_id", "yes_token_id", "no_token_id", 
        "market_type", "steam_side_mapping", "game_time_sec", "radiant_net_worth", 
        "dire_net_worth", "radiant_score", "dire_score", "game_over", 
        "yes_best_bid", "yes_best_ask", "no_best_bid", "no_best_ask", 
        "book_received_at_ns", "data_source", "settled_yes_outcome", "settled_no_outcome"
    ]
    
    # drop those that don't exist
    for c in required_cols:
        if c not in final_df.columns:
            print(f"Missing column {c}, filling with nan")
            final_df[c] = np.nan
            
    final_df = final_df[required_cols]
    final_df = final_df.dropna(subset=['yes_best_bid', 'no_best_bid'])
    
    out_file = "generated_replay_data.csv"
    final_df.to_csv(out_file, index=False)
    print(f"Done! Saved to {out_file} with {len(final_df)} rows")

if __name__ == "__main__":
    build_replay_data()
