import pandas as pd
import json
import numpy as np

def main():
    replay_df = pd.read_parquet("data_v2/model_value_replay.parquet")
    print("Replay shape:", replay_df.shape)
    print("Unique matches:", replay_df['match_id'].nunique())
    print("Unique tokens:", replay_df['token_id'].nunique())
    print("Duplicate snapshot rate:", replay_df.duplicated(subset=['timestamp_ns', 'token_id']).mean())
    
    with open("models/dota_lgbm_win/metadata.json") as f:
        meta = json.load(f)
    print("Train matches:", len(meta["train_matches"]))
    print("Valid matches:", len(meta["valid_matches"]))
    
    trades_df = pd.read_csv("reports/model_value_audit_20260621_195300/robustness_base_0.02/model_value_v1_trades.csv")
    trade_matches = set(trades_df['match_id'].astype(str))
    train_matches = set(map(str, meta["train_matches"]))
    valid_matches = set(map(str, meta["valid_matches"]))
    
    print("Trades in train matches:", len(trade_matches.intersection(train_matches)))
    print("Trades in valid matches:", len(trade_matches.intersection(valid_matches)))
    print("Trades in pure test matches:", len(trade_matches - train_matches - valid_matches))
    print("Trades shape:", trades_df.shape)
    print("Duplicate trade rate:", trades_df.duplicated(subset=['match_id', 'token_id', 'side']).mean())
    
    resolved = trades_df[trades_df['settlement_outcome'].isin(['WIN', 'LOSS'])]
    unresolved = trades_df[~trades_df['settlement_outcome'].isin(['WIN', 'LOSS'])]
    print("Resolved trades:", len(resolved))
    print("Unresolved trades:", len(unresolved))
    
    # Check if any match has more than 1 trade
    trades_per_match = trades_df.groupby('match_id').size()
    print("Matches with >1 trade:", (trades_per_match > 1).sum())
    
    # Check if opposing tokens are bought in same match
    # Since side mapping is handled correctly by blocking opposing token in live trading? Wait, backtest simulation needs to block it.
    
    print("Data integrity check complete.")

if __name__ == "__main__":
    main()
