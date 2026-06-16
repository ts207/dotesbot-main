import sys

targets = ['ML_ARBITRAGE', 'dota_fair_model', 'load_bundle', 'team_stats', 'build_feature_row']

with open('main.py', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        if any(t in line for t in targets):
            print(f"{i+1}: {line.strip()}")
