import sys
from pathlib import Path
REPO_ROOT = Path('.').resolve()
sys.path.insert(0, str(REPO_ROOT))

from scripts.backtest_value_engine import (
    load_snapshots, load_markets, load_books, load_outcomes, replay, _params
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

joined = {match_id: snapshots[match_id]}
trades, cov, unres, raw, rejects = replay(
    snapshots=joined,
    markets=markets,
    book=book,
    outcomes=outcomes,
    outcome_sources=outcome_sources,
    params=_params(),
    confirm=False
)
print(f"Trades: {len(trades)}")
print(f"Rejects: {rejects}")
