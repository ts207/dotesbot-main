# CLAUDE.md — Dota 2 / Polymarket trading bot

Operational + architectural reference. Deep history lives in the auto-memory
(`MEMORY.md` index). This file is the "how it works / how to run it / what not to
break" guide. Read it before touching anything.

---

## 0. What this is
A live-betting bot for **Dota 2 markets on Polymarket CLOB**. It reads the live
Steam game state (net-worth lead per second) and trades the Polymarket order book.
The validated edge is **informational**: the net-worth lead predicts the game/series
winner, and the market is sometimes slow to price it. Two strategies exploit this
(see §3). Everything is **hold-to-(settlement/convergence)** — no in-game momentum
scalping (that was tested and is dead: price LEADS the Steam feed).

## 1. Current state (keep this updated)
- **Account: ~$5.45, bot HALTED** (`ENABLE_REAL_LIVE_TRADING=false`). Down from
  $115.98 — **the loss was MANUAL cockpit trading, not the bot** (the bot went 5/5
  on its own settled trades). See §8.
- Strategies built + validated; **value bot live-ready, decisive-swing armed-but-off**.
- Activation needs two things YOU own: **fund (~$200-300) + commit to bot-only.**

## 2. How to run it
- **RUN `python3 supervisor.py`** — NOT `main.py` directly. The supervisor is a
  watchdog that launches + auto-restarts `main.py`, `auto_series_binder.py`,
  `settlement_shadow.py` on death or heartbeat staleness.
- **To deploy a code change: restart the affected process** (kill it; the supervisor
  relaunches with new code). A running Python process does NOT pick up edits.
  - `main.py` changes (engines, executor, exit logic) → kill `python3 main.py`.
  - binder changes → kill `auto_series_binder`.
- **`python3 monitor.py`** — one health/risk pass (NAV, drawdown, errors,
  concentration, stuck positions); appends `logs/nav_history.csv` (equity curve),
  exit code 0/1/2. This is the autonomous supervisor; run it on a schedule.
- **`python3 cockpit.py "<team>"`** — manual trading TUI (see §6). ⚠️ manual trading
  is what wiped the account; use with extreme care or not at all.
- Boot does a startup reconcile (now scoped to position tokens, ~15s).

## 3. The two strategies
### Value bot (`value_engine.py`) — LIVE-READY
Back the net-worth **leader** when the model `fair − ask ≥ edge`, **hold to settle**.
- Trades MAP_WINNER (+ game-3-proxy MATCH_WINNER). Gates: `data_source=top_live`,
  `game_time≥600`, `|lead|≥3000`, `ask≤0.84`, orientation guard, `edge≥0.10`,
  **`fair≥0.80` conviction floor (`VALUE_MIN_FAIR`, 2026-06-03)**.
- **Conviction floor (2026-06-03 sweep):** the edge is concentrated in high-conviction
  trades (fair 0.8+ won ~100%; 0.6–0.7 was a coin-flip that diluted). Gating on FAIR
  (not raw lead — lead is already inside fair) moved the backtest from P(ROI>0)=0.89 /
  CI straddles 0 → **0.98 / CI off 0**, win 70%→83%. `VALUE_MIN_FAIR=0.0` disables.
  See `scripts/value_sweep.py`, `stress_edge.py`, memory `stress_test_verdicts_2026_06_03`.
- Backtest: ~+16.7% ROI / 67% win (full data, pre-conviction-gate). The "+21% filtered"
  config existed (price-floor/edge-cap/time-cap) but was **disabled per user** — those
  filters are env knobs (`VALUE_MIN_PRICE` etc.), currently permissive.
- Per-match cap `VALUE_MAX_PER_MATCH=6` (HARD — prevents the over-stack that dumped
  $50 into one match; see §8).

### Decisive-swing ML sniper (`decisive_swing_engine.py`) — WIRED, OFF
User-found edge: BO3 moneyline is **stale after a game-ending swing** — buy the
near-certain winner's ML below series-fair, exit at map-end convergence.
- Backtest +14.8% ROI / 82% at `DSWING_LEAD=6000` (enter early = max staleness).
- `DSWING_ENABLED=false`. To arm: `DSWING_ENABLED=true` + live + restart.
- **Depends on reliable series state** (game#/score) for off-decider gating — now
  fixed (see §5 binder). Exit risk: thin ML book may have no bid at map-end.
- **Combined (both strategies): +16.4% ROI / 76% win / n=62.**

## 4. The win-prob model (`winprob.py`) — the `fair` source
`winprob.fair(lead, game_time_sec, elo_diff, lead_slope, draft_h2h)` →
P(team with this net-worth lead wins). Symmetric logistic on **1000 OpenDota pro
matches** (`winprob_full_v2`, AUC 0.86, calibrated on 3268 real matches).
- Features: gold lead, minute, **Elo** (resolve by team NAME — feed gives id ~3%),
  **lead trajectory**, **draft-H2H** (all clamped/shrunk so none can break a real
  lead's price). Draft predicts pre-game but is mostly redundant by the 3k gate.
- Artifacts: `logs/winprob_model.json` (coefs), `logs/team_elo*.json`,
  `logs/opendota_hero_matchups.json`. Refit: `fit_winprob.py`.
- **Pure-math at runtime (no sklearn)** — safe in the hot loop.

## 5. Key subsystems
- **Book feed** (`book_refresh.py` + `proactive_refresh_loop` in main): WS is dead
  (snapshot-only), so **REST is the primary book source**, refreshing live-match
  tokens every 2s. Has a **90s grace window** so games flickering out of GetTopLive
  (draft/gt=0) don't blank the book. Book coverage is the historical bottleneck.
- **Binder** (`auto_series_binder.py`): maps Polymarket markets → live Steam matches.
  Now **derives real series state** (`derive_series_state`) from resolved game
  markets → fixes the stale "G1 0-0" bug → enables decider detection (map3, dswing).
- **Exit** (`live_exit_engine.py`): `trader_kind=value` (used by both strategies) =
  hold-to-settle, exits at game_over / max-hold / **catastrophe-salvage** (bid<0.12
  AND net-worth confirms losing — won't dump a winner on a flip).
- **Executor** (`live_executor.py`): `try_buy_value` (FAK, per-match cap, budget
  rails, records delayed orders). `LiveCLOBClient`, sig_type=3 proxy wallet.

## 6. Cockpit (manual TUI) — handle with care
`cockpit.py`: shows GetTopLive state, both order books, the **win-prob model
fair+edge**, account panel (cash + positions + P&L), an **orientation-flip warning**,
and MAP/SERIES/decider labels. `d` toggles decider-mode. Query is word-based.
⚠️ **Manual cockpit trading drained the account** — every position decision should
use the Dota match state, not vibes.

## 7. The operating model (bot / Claude / user)
- **Bot = hands (reflex timescale):** entries, sizing, hard caps, catastrophe exit.
  Claude does NOT place/size individual trades — too slow (0.5s loop) + fallible.
- **Claude = brain (decision timescale):** supervision (run monitor on a cadence),
  position DECISIONS (hold/cut/redeem/de-risk), config tuning in validated ranges,
  emergency halt, diagnostics. **VERIFY before acting** (multi-read) — balance/feed
  reads are unreliable (see §8). **Alert + wait** on anything capital-affecting
  (scaling, arming a strategy). Never override the deterministic caps.
- **User = principal:** funding, scaling, strategy go-live, checks in.

## 8. HARD-WON LESSONS (do not relearn these the expensive way)
- **The account died to MANUAL trading**, not the bot. If managing, the bot is the
  ONLY thing trading. No discretionary cockpit bets.
- **The balance API lies.** It returned a transient "$102" (real was different) and
  "$5.45" (real). **Always read cash 3-6× and require stability** before believing/
  acting on it. NAV from `live_positions.json` alone is wrong — it misses on-chain +
  cockpit tokens; value from a token scan.
- **Over-stack bug (fixed):** the value path had no per-match cap → dumped ~$50 into
  one match. `VALUE_MAX_PER_MATCH` is the hard fix; never add a live order path
  without a per-match cap + recording `delayed` orders.
- **Orientation flip:** the binder can bind a token to the wrong team → buying the
  LOSER looks like a screaming value buy. Guard: `|lead|>5000 & ask<0.35` rejects.
  Keep it. A cheap "leader" token = flip or market-disagrees → don't trust.
- **425/500 order errors were Polymarket-side**, not our auth (auth + signing verified;
  clock fine). The Steam feed throws transient 500s — now retried.
- **Hold-to-settle beats active exits** (proven). Don't add TP/SL stops — they sell
  winners on dips (Inner Circle dipped to 0.52 then WON). Only the catastrophe
  salvage (bid<0.12 + net-worth-confirmed) is allowed.
- **Backtest reality:** only ~130 matches have snapshots+book+mapping (the rest are
  pub games with no Polymarket market — unrecoverable). Samples are small (n=17-62);
  treat ROI as directional, not precise. The edge is real but unproven at scale.
- **Restart churn:** most pid changes are SIGTERM (-15 = manual kills), not crashes.
  Supervisor only SIGKILLs (-9) / logs HUNG. Boot does a slow-ish reconcile.

## 9. Risk rails / kill switches
- `.env`: `MAX_TRADE_USD`, `VALUE_MAX_PER_MATCH`, `MAX_TOTAL_LIVE_USD`,
  `MAX_DAILY_DRAWDOWN_USD`, `MAX_OPEN_POSITIONS`. Scale these to ~30%/20% of fund.
- **Kill switch:** `ENABLE_REAL_LIVE_TRADING=false` + restart → paper instantly.
- When funding: set `MAX_TRADE_USD` back to ~$6 (it's at 15 from an old 3× bump),
  `MAX_TOTAL_LIVE_USD` to ~30% of fund.

## 10. Conventions
- End commit messages: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Don't commit `.env` (it holds real keys).
- Skip 0-byte parquet files in `data_v2/` (a known write bug) when reading.
- Update §1 (current state) and the memory when state materially changes.
