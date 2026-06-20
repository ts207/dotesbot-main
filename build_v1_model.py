import json

metadata = {
  "model_name": "model_value_edge_v1_residual",
  "model_class": "residual_tree",
  "residual_mode": True,
  "uses_market_price": True,
  "uses_game_time": True,
  "uses_objectives": False,
  "uses_orderbook_dynamics": True,
  "deployment_status": "paper_only"
}

features = [
  "market_mid",
  "ask",
  "spread",
  "game_time_sec",
  "token_net_worth_lead",
  "token_score_margin",
  "token_net_worth_lead_per_min"
]

model = {
  "name": "v1_residual_booster",
  "tree_info": [
    {
      "tree_index": 0,
      "tree_structure": {
        "split_feature": "token_net_worth_lead",
        "threshold": 0.0,
        "left_child": {
          "leaf_value": -0.05
        },
        "right_child": {
          "leaf_value": 0.05
        }
      }
    }
  ]
}

with open("models/dota_lgbm_win/metadata.json", "w") as f:
    json.dump(metadata, f, indent=2)

with open("models/dota_lgbm_win/features.json", "w") as f:
    json.dump(features, f, indent=2)

with open("models/dota_lgbm_win/model.json", "w") as f:
    json.dump(model, f, indent=2)

print("v1 model written")
