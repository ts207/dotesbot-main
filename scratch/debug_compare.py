import sys
from pathlib import Path
sys.path.insert(0, str(Path('.').resolve()))
from scripts.backtest_value_engine import (
    load_snapshots, load_markets, load_books, load_outcomes, replay, _params, fair_price, winprob
)
import scripts.backtest_value_engine as bve

match_id = "8830206341"
markets, _ = load_markets()
snapshots = load_snapshots({match_id})

orig_fair = fair_price
def tracked_fair(row, direction, lead, history):
    ns = int(row.get('received_at_ns') or 0)
    if ns == 1780064523468384256:
        print(f"REPLAY: len(history)={len(history)}")
        target = ns - 300_000_000_000
        past = None
        for hist_ns, hist_lead in history:
            if hist_ns <= target:
                past = hist_lead
            else:
                break
        print(f"REPLAY: past={past}, lead={lead}")
    return orig_fair(row, direction, lead, history)

bve.fair_price = tracked_fair

# 1. Run replay
joined = {match_id: snapshots[match_id]}
tokens = {str(markets[match_id]["yes_token_id"]), str(markets[match_id]["no_token_id"])}
book = load_books(tokens)
outcomes, outcome_sources = load_outcomes()
bve.replay(snapshots=joined, markets=markets, book=book, outcomes=outcomes, outcome_sources=outcome_sources, params=_params(), confirm=False)

# 2. Run isolated
print("ISOLATED:")
from collections import deque
from scripts.backtest_value_engine import signal_side

mapping = markets[match_id]
history2 = deque(maxlen=4000)

for row in snapshots[match_id]:
    ns = int(row.get('received_at_ns') or 0)
    lead = row.get('radiant_lead')
    game_time = row.get('game_time_sec')
    if row.get('game_over') or game_time is None or lead is None:
        continue
    lead = int(lead)
    history2.append((ns, lead))
    
    if ns == 1780064523468384256:
        print(f"ISOLATED: len(history)={len(history2)}")
        target = ns - 300_000_000_000
        past = None
        for hist_ns, hist_lead in history2:
            if hist_ns <= target:
                past = hist_lead
            else:
                break
        print(f"ISOLATED: past={past}, lead={lead}")
        side, direction = signal_side(mapping, lead)
        orig_fair(row, direction, lead, history2)

