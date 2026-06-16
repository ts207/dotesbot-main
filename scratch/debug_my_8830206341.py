import sys
import json
from collections import deque
from pathlib import Path
REPO_ROOT = Path('.').resolve()
sys.path.insert(0, str(REPO_ROOT))

from scripts.backtest_value_engine import (
    load_snapshots, load_markets, load_books, load_outcomes, _params, fair_price, signal_side, book_at
)

match_id = "8830206341"
markets, _ = load_markets()
snapshots = load_snapshots({match_id})
outcomes, outcome_sources = load_outcomes()

tokens = set()
if match_id in markets:
    tokens.add(str(markets[match_id]["yes_token_id"]))
    tokens.add(str(markets[match_id]["no_token_id"]))
book = load_books(tokens)

mapping = markets[match_id]
yes_token = str(mapping["yes_token_id"])
no_token = str(mapping["no_token_id"])
params = _params()
history = deque(maxlen=4000)

for row in snapshots[match_id]:
    ns = int(row.get("received_at_ns") or 0)
    game_time = row.get("game_time_sec")
    lead = row.get("radiant_lead")
    if row.get("game_over") or game_time is None or lead is None:
        continue
    lead = int(lead)
    history.append((ns, lead))
    
    if ns == 1780064523468384256:
        side, direction = signal_side(mapping, lead)
        fair = fair_price(row, direction, lead, history)
        token = yes_token if side == "YES" else no_token
        entry_book = book_at(book, token, ns)
        ask = float(entry_book["best_ask"])
        edge = fair - ask
        print(f"Time: {game_time}, Lead: {lead}, Side: {side}, Dir: {direction}, Fair: {fair}, Ask: {ask}, Edge: {edge}")

