# Dota 2 / Polymarket Trading Bot

This is an automated trading bot designed for Dota 2 markets on Polymarket. It listens to live game state changes via the Steam API, evaluates a trained win-probability model, and executes hold-to-settle trades on the Polymarket CLOB.

**IMPORTANT: This is the high-level README. For architecture, strategies, operational procedures, and risk management, read [`AGENTS.md`](./AGENTS.md). It is the single source of truth.**

## How to Run

Do NOT run `main.py` directly. The system relies on a supervisor to manage child processes and restart them on hangs or crashes.

To start the bot:
```bash
python3 supervisor.py
```
The supervisor manages two core processes:
1.  **bot** (`main.py`): The core trading loop.
2.  **binder** (`auto_series_binder.py`): The market discovery and matching service.

To monitor the bot's health, run the monitor script on a schedule:
```bash
python3 monitor.py
```

## Active Strategies

The bot primarily relies on structural value discrepancies rather than short-horizon scalps. The active strategies are:

*   **Value Strategy:** Backs the net-worth leader when the model fair price significantly exceeds the order book ask. Conviction-gated and holds to settlement. (LIVE)
*   **Event-Triggered Value:** Fires on actual Dota events (kills, tower, NW swings) when the win-probability fair diverges from the book, combining fast reactivity with value conviction. (LIVE)
*   **Decisive-Swing Strategy:** SNIPER strategy that buys the BO3 moneyline when a specific map's net worth lead crosses a near-certain game-ending threshold. (CURRENTLY DISABLED by default).

For historical context on older event-driven strategies (which have been superseded), see `docs/historical/`.

## Configuration

Bot behavior, thresholds, and execution limits are configured via the `.env` file. Copy `.env.example` to `.env` and fill in your Polymarket credentials and Steam API key. See `docs/effective_config.md` for how configuration is loaded and validated at runtime.
