import pandas as pd
import numpy as np

data = {
    "timestamp_ns": [1700000000000000000, 1700000060000000000, 1700000120000000000],
    "match_id": ["8853138000", "8853138000", "8853138000"],
    "market_id": ["m1", "m1", "m1"],
    "yes_token_id": ["t1_yes", "t1_yes", "t1_yes"],
    "no_token_id": ["t1_no", "t1_no", "t1_no"],
    "market_type": ["MAP_WINNER", "MAP_WINNER", "MAP_WINNER"],
    "steam_side_mapping": ["normal", "normal", "normal"],
    "game_time_sec": [600, 660, 720],
    "radiant_net_worth": [15000, 16000, 17500],
    "dire_net_worth": [14000, 14500, 14500],
    "radiant_score": [5, 6, 8],
    "dire_score": [4, 4, 4],
    "game_over": [False, False, False],
    "yes_best_bid": [0.55, 0.60, 0.65],
    "yes_best_ask": [0.60, 0.65, 0.70],
    "no_best_bid": [0.35, 0.30, 0.25],
    "no_best_ask": [0.40, 0.35, 0.30],
    "book_received_at_ns": [1700000000000000000, 1700000060000000000, 1700000120000000000],
    "data_source": ["top_live", "top_live", "top_live"],
    "settled_yes_outcome": ["WIN", "WIN", "WIN"],
    "settled_no_outcome": ["LOSS", "LOSS", "LOSS"]
}

df = pd.DataFrame(data)
df.to_csv("dummy_replay.csv", index=False)
