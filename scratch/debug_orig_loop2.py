import sys
from pathlib import Path
sys.path.insert(0, str(Path('.').resolve()))
from scripts.backtest_value_engine import (
    load_snapshots, load_markets, load_books, load_outcomes, replay, _params
)
import scripts.backtest_value_engine as bve

# Let's monkey-patch backtest_value_engine to print the edge evaluation for 1780064523468384256
orig_fair_price = bve.fair_price
def tracked_fair_price(row, direction, lead, history):
    fair = orig_fair_price(row, direction, lead, history)
    ns = int(row.get('received_at_ns') or 0)
    if ns == 1780064523468384256:
        print(f"TRACKED: ns={ns}, lead={lead}, fair={fair}")
    return fair

bve.fair_price = tracked_fair_price

match_id = "8830206341"
markets, _ = load_markets()
snapshots = load_snapshots({match_id})
outcomes, outcome_sources = load_outcomes()

tokens = set()
tokens.add(str(markets[match_id]["yes_token_id"]))
tokens.add(str(markets[match_id]["no_token_id"]))
book = load_books(tokens)

joined = {match_id: snapshots[match_id]}
trades, cov, unres, raw, rejects = bve.replay(
    snapshots=joined,
    markets=markets,
    book=book,
    outcomes=outcomes,
    outcome_sources=outcome_sources,
    params=_params(),
    confirm=False
)
print(f"Trades: {len(trades)}")
print(f"Raw signals: {raw}")
