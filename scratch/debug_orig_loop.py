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
print(f"Raw signals: {raw}")
if trades:
    print(f"Trade: {trades[0]['entry_book_ts']}")
