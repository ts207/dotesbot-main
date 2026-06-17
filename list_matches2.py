import json
import os

try:
    with open("logs/bound_markets.json") as f:
        print("bound_markets:", f.read()[:500])
except Exception as e:
    pass

try:
    with open("logs/runtime_markets.json") as f:
        print("runtime_markets:", f.read()[:500])
except Exception as e:
    pass
    
from storage_v2 import load_bound_markets
markets = load_bound_markets()
print("Total bound markets:", len(markets))
for m in markets:
    print(m.get("name"), m.get("match_id"), m.get("game_time_sec"), m.get("radiant_lead"))
