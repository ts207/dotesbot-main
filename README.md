# Dota 2 / Polymarket Trading Bot

This is an automated trading bot designed for Dota 2 markets on Polymarket. It listens to live game state changes via the Steam API, evaluates calibrated win-probability and strategy models, and can route paper, dry-live, or explicitly enabled real-live orders through the Polymarket CLOB.

**IMPORTANT: This is the high-level README. For architecture, strategies, operational procedures, and risk management, read [`AGENTS.md`](./AGENTS.md). It is the single source of truth.**

## How to Run

Do NOT run `main.py` directly. The system relies on a supervisor to manage child processes and restart them on hangs or crashes.

To start the bot:
```bash
python3 supervisor.py
```
The supervisor manages the runtime processes that need to stay alive together:
1.  **bot** (`main.py`): The core trading loop.
2.  **binder** (`auto_series_binder.py --loop`): The market discovery and matching service.
3.  **shadow** (`settlement_shadow.py --loop`): The settlement accounting shadow loop.
4.  **monitor** (`monitor.py --loop`): Health and risk monitoring.

For an on-demand health check outside the supervised loop, run:
```bash
python3 monitor.py
```

## Active Strategies

The bot primarily relies on structural value discrepancies rather than short-horizon scalps. The active strategies are:

*   **Value Strategy:** Backs the net-worth leader when the model fair price significantly exceeds the order book ask. Conviction-gated and generally managed as a settlement/value thesis. (LIVE when enabled by config)
*   **Event-Triggered Value:** Fires on actual Dota events (kills, tower, NW swings) when the win-probability fair diverges from the book, combining fast reactivity with value conviction. (LIVE when enabled by config)
*   **Decisive-Swing Strategy:** SNIPER strategy that buys the BO3 moneyline when a specific map's net worth lead crosses a near-certain game-ending threshold, then exits on map-end convergence. (Controlled by `DSWING_ENABLED`)

Legacy compound event detection remains in `event_detector.py` for diagnostics and historical analysis, but legacy event entries are disabled by default. See `AGENTS.md` for the current strategy pipeline.

## Configuration

Bot behavior, thresholds, and execution limits are configured via the `.env` file. Copy `.env.example` to `.env` and fill in your Polymarket credentials and Steam API key. See `docs/effective_config.md` for how configuration is loaded and validated at runtime.
