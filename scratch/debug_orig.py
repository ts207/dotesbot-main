import sys
from pathlib import Path
sys.path.insert(0, str(Path('.').resolve()))

from scripts.backtest_value_engine import (
    load_snapshots, load_markets, load_books, load_outcomes, replay, _params
)

match_id = "8830206341"
markets, _ = load_markets()
snapshots = load_snapshots({match_id})
outcomes, outcome_sources = load_outcomes()

tokens = set()
tokens.add(str(markets[match_id]["yes_token_id"]))
tokens.add(str(markets[match_id]["no_token_id"]))
book = load_books(tokens)
params = _params()

print("Original Replay Step by Step:")
from collections import deque
from scripts.backtest_value_engine import winprob, final_book_yes_won, resolve_yes_won, signal_side, book_at, fair_price
import math

mapping = markets[match_id]
yes_token = str(mapping["yes_token_id"])
no_token = str(mapping["no_token_id"])

yes_won, source = resolve_yes_won(match_id, mapping, book, outcomes, outcome_sources)
entered = False
history = deque(maxlen=4000)

for row in snapshots[match_id]:
    ns = int(row.get("received_at_ns") or 0)
    if ns == 1780064523468384256:
        print("FOUND ROW!")
        
    game_time = row.get("game_time_sec")
    lead = row.get("radiant_lead")
    if row.get("game_over") or game_time is None or lead is None:
        continue
    
    lead = int(lead)
    history.append((ns, lead))
    
    if ns == 1780064523468384256:
        if game_time < params["min_time"]:
            print("game_too_early")
        if game_time > params["max_time"]:
            print("game_too_late")
        if abs(lead) < params["min_lead"]:
            print("lead_too_small")
            
        side, direction = signal_side(mapping, lead)
        token = yes_token if side == "YES" else no_token
        entry_book = book_at(book, token, ns)
        
        book_age_ms = (ns - int(entry_book["received_at_ns"])) / 1_000_000
        if book_age_ms > params["book_age_ms"]:
            print("book_stale")
            
        ask = entry_book.get("best_ask")
        if ask is None or (isinstance(ask, float) and math.isnan(ask)):
            print("missing_ask")
        ask = float(ask)
        if ask > params["max_price"]:
            print("price_too_high")
        if ask < params["min_price"]:
            print("price_too_low")
            
        if abs(lead) > params["flip_lead"] and ask < params["flip_ask_floor"]:
            print("orientation_flip")
            
        fair = fair_price(row, direction, lead, history)
        edge = fair - ask
        if fair < params["min_fair"]:
            print(f"fair_too_low: {fair} < {params['min_fair']}")
        if edge < params["min_edge"]:
            print(f"edge_too_small: {edge} < {params['min_edge']}")
        if edge > params["max_edge"]:
            print("edge_too_large")
            
        print(f"Fair: {fair}, Edge: {edge}, Ask: {ask}")
