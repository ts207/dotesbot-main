Agreed. The previous plan was too generic. I inspected the actual repo paths and active code. This is a repo-tailored plan based on the implementation, not an abstract trading-bot roadmap.

## What the repo actually is now

`dotesbot-main` is no longer just a Dota event-latency bot. The active runtime has four layers:

1. **Feed/binding layer**

   * Steam TopLive / LiveLeague / RealtimeStats ingestion.
   * Polymarket CLOB WebSocket book ingestion.
   * Polymarket market discovery and Steam match binding.

2. **Signal layer**

   * Legacy tactical event signal engine.
   * `VALUE_EDGE` net-worth/fair-value engine.
   * `EVENT_CONTINUATION_EDGE` / `EVENT_REVERSAL_EDGE`.
   * `DSWING` match-winner convergence sniper.

3. **Allocation/execution layer**

   * Strategy allocator chooses among competing candidates.
   * Paper trader simulates best-ask entry and best-bid exit.
   * Optional guarded live executor submits capped CLOB orders.

4. **Logging/research layer**

   * CSV loggers, markouts, latency rows, strategy signals, allocator logs, paper/live attempts, rich context, source delay.

`main.py` explicitly says current active paper strategies are `VALUE`, `DSWING`, and `EVENT_TRIGGERED_VALUE`; legacy event entries are disabled unless explicitly enabled. 

---

# Repo-specific priority plan

## Phase 1 — Stabilize configuration first

### Why this is first

The actual repo has major config drift. `.env.example` says:

```text
MAX_STEAM_AGE_MS=1500
MAX_BOOK_AGE_MS=750
MIN_LAG=0.08
MIN_EXECUTABLE_EDGE=0.03
MAX_SPREAD=0.06
```



But `config.py` defaults to much looser values, including:

```text
MAX_STEAM_AGE_MS=25000
MAX_BOOK_AGE_MS=90000
MIN_LAG=0.05
MIN_EXECUTABLE_EDGE=0.05
MAX_SPREAD=0.15
```

 

This means the bot behaves differently depending on whether `.env` exists. For a latency-sensitive trading system, that is unacceptable.

### Exact implementation

Create:

```text
runtime_config.py
```

with typed sections:

```python
@dataclass(frozen=True)
class FeedConfig:
    steam_poll_seconds: float
    max_steam_age_ms: int
    max_source_update_age_sec: float
    require_top_live_for_signals: bool

@dataclass(frozen=True)
class BookConfig:
    max_book_age_ms: int
    max_spread: float
    min_ask_size_usd: float

@dataclass(frozen=True)
class PaperConfig:
    paper_trade_size_usd: float
    paper_slippage_cents: float
    paper_execution_delay_ms: int
    max_open_usd_per_match: float

@dataclass(frozen=True)
class LiveConfig:
    live_mode: Literal["off", "dry_run", "real"]
    max_total_live_usd: float
    max_trade_usd: float
    max_open_positions: int
    max_daily_drawdown_usd: float

@dataclass(frozen=True)
class StrategyConfig:
    value_enabled: bool
    dswing_enabled: bool
    event_triggered_value_enabled: bool
```

Then replace direct scattered `os.getenv()` reads in:

```text
config.py
value_engine.py
decisive_swing_engine.py
event_triggered_value_engine.py
live_executor.py
live_exit_engine.py
signal_engine.py
```

with `config.<section>.<field>`.

### Deliverables

```text
runtime_config.py
check_config.py
docs/effective_config.md
```

### Acceptance criteria

`python check_config.py` must print:

```text
setting
runtime value
source: env/default
safe_for_paper
safe_for_dry_live
safe_for_real_live
```

Real live mode must fail closed if any required live setting comes from unsafe default values.

---

## Phase 2 — Centralize policy because the repo already has duplicated gates

### Current repo problem

The same checks exist in several places:

* `signal_engine.py` checks freshness, book age, spread, caps, game phase, event type, lag, edge, and hold-to-settle bypasses.
* `value_engine.py` separately checks TopLive, game time, book age, ask caps, lead, fair edge, and orientation flip.
* `event_triggered_value_engine.py` separately checks primitive event type, book age, ask min/max, fair delta, event reprice gap, and edge.
* `live_executor.py` repeats mapping validation, event allowlist, cadence quality, spread, book age, price caps, live budget, and edge/lag gates.

The live executor comments show this duplication already caused valid hold-to-settle signals to be rejected because live had separate short-horizon edge/lag gates. 

### Exact implementation

Create:

```text
execution_policy.py
```

Use it in this order:

1. `live_executor.py`
2. `paper_trader.py`
3. `value_engine.py`
4. `event_triggered_value_engine.py`
5. `decisive_swing_engine.py`
6. legacy `signal_engine.py`

Core API:

```python
@dataclass(frozen=True)
class PolicyInput:
    mode: Literal["paper_research", "paper_live_parity", "dry_live", "real_live"]
    strategy_kind: str
    market_type: str
    token_id: str
    side: str
    signal: dict
    game: dict
    mapping: dict
    book: dict | None
    now_ns: int

@dataclass(frozen=True)
class PolicyResult:
    allowed: bool
    reason: str
    would_pass_live: bool
    live_skip_reason: str
    paper_only_bypass: bool
    price_cap: float | None
    size_usd: float | None
    risk_tags: tuple[str, ...]
```

### Move these gates into the policy module

```text
mapping_valid
unsupported_market_type
non_top_live_source
steam_stale
source_update_stale
book_missing
book_stale
missing_bid_or_ask
spread_too_wide
insufficient_ask_size
ask_above_max_fill
terminal_price_chase
orientation_flip_suspected
strategy_disabled
event_not_allowed
cadence_schema_missing
cadence_quality_bad
event_quality_too_low
edge_too_small
lag_too_small
hold_to_settle_edge_lag_bypass
max_open_positions
max_total_live_usd
max_open_usd_per_match
daily_drawdown_breaker
```

### Acceptance criteria

Every signal/trade attempt must log:

```text
policy_allowed
policy_reason
would_pass_live
live_skip_reason
paper_only_bypass
policy_version
risk_tags
```

`storage.py` already has signal columns for `would_pass_live_gates`, `live_skip_reason`, and `paper_only_bypass`, so this fits the repo instead of adding an alien concept. 

---

## Phase 3 — Split strategy contracts out of code

### Current repo reality

`event_triggered_value_engine.py` already contains a strategy contract function. It distinguishes reversal vs continuation and defines edge type, horizon, entry trigger, exit trigger, primary metric, promotion rule, and disable rule. 

That is good, but it is hardcoded inside the engine.

`DSWING` also embeds a contract in its dataclass: edge type is `map_end_series_convergence`, target horizon is `map_end`, exit trigger is `map_end_convergence`, and the primary metric is `exit_bid - entry_price`. 

### Exact implementation

Create:

```text
strategies/
  value_edge.yaml
  event_continuation_edge.yaml
  event_reversal_edge.yaml
  dswing.yaml
  legacy_event.yaml
```

Example:

```yaml
strategy_kind: EVENT_CONTINUATION_EDGE
version: 2026-06-16
enabled_paper: true
enabled_dry_live: false
enabled_real_live: false
edge_type: event_repricing
target_horizon: repricing_120s
expected_hold_sec: 120
entry_trigger: fair_delta_used - market_reprice
exit_trigger: markout_target / timeout / thesis_invalidation
primary_metric: 120s_realizable_markout
secondary_metric: incremental_settlement_roi_over_value
promotion_rule: positive_markout_or_incremental_settlement_roi_over_value
disable_rule: negative_event_markout_or_no_incremental_edge
```

Then replace `_strategy_contract()` in `event_triggered_value_engine.py` with:

```python
contract = strategy_registry.get("EVENT_REVERSAL_EDGE" if is_reversal else "EVENT_CONTINUATION_EDGE")
```

### Acceptance criteria

No strategy metadata should be hardcoded inside signal constructors. Engines should calculate signals; contracts should live in `strategies/*.yaml`.

---

## Phase 4 — Make paper results live-comparable

### Current repo reality

Paper execution is good but not yet fully live-comparable.

`PaperTrader` fills at the current best ask and exits at bid, which is correct for taker simulation.  

But some paper strategy logic intentionally bypasses live freshness gates. For example, `signal_engine.py` only enforces Steam/book staleness when `ENABLE_REAL_LIVE_TRADING` is true.  

### Exact implementation

Add a paper mode enum:

```text
PAPER_MODE=research|live_parity|shadow_live
```

Behavior:

```text
research     = allow counterfactual paper entries but label live rejection reason
live_parity  = reject anything real live would reject
shadow_live  = do not enter paper position unless dry-live policy would submit
```

Modify:

```text
paper_trader.py
main.py
storage.py
analyze_logs.py
```

### Add paper result splits

Reports must show:

```text
all_paper_pnl
live_parity_paper_pnl
paper_only_pnl
stale_book_pnl
stale_steam_pnl
wide_spread_pnl
mapping_risk_pnl
```

### Acceptance criteria

A profitable paper result is not considered deployable unless the `live_parity` subset is also profitable.

---

## Phase 5 — Fix live state persistence before expanding live usage

### Current repo reality

Live positions are stored in `logs/live_positions.json`. `LivePositionStore` has rich state fields and active-state tracking, but it is still JSON-file state. 

The reconciliation layer treats CLOB token balances as source of truth and reopens or closes local positions accordingly.  It also avoids scanning every mapping token because that previously made boot take about 250 seconds. 

This is already operationally mature, but file-state remains fragile.

### Exact implementation

Do **not** jump straight to Postgres. Add SQLite beside JSON first.

Create:

```text
state_store.py
schema.sql
migrations/001_initial.sql
```

Tables:

```sql
live_positions
live_orders
live_reconciliations
paper_positions
paper_orders
strategy_signals
allocation_decisions
policy_decisions
mapping_snapshots
feed_health
```

Dual-write first:

```text
JSON remains source of truth
SQLite receives mirrored writes
daily checker compares JSON vs SQLite
after N clean days, SQLite becomes source of truth
```

### Acceptance criteria

Restart recovery should not depend on loosely replaying CSVs or trusting one JSON file. The bot should be able to answer:

```text
What positions are open?
Which orders are pending?
Which mappings created them?
Which policy approved them?
Which strategy created them?
```

---

## Phase 6 — Harden mapping and orientation

### Current repo reality

Mapping is one of the highest-risk areas, and the repo already knows it.

Market discovery maps `outcomes[i]` to `clobTokenIds[i]`, which is the right fix for token-order ambiguity. 

Mapping validation rejects missing fields, placeholders, unsupported market types, low confidence, equal yes/no tokens, same-team mappings, and duplicate active match IDs.  

The value engine and live executor both contain orientation-flip guards because buying the wrong token can look like a huge value opportunity.  

### Exact implementation

Create:

```text
mapping_audit.py
mapping_quarantine.py
```

Run `mapping_audit.py` before live startup and every mapping refresh.

Checks:

```text
placeholder token/match IDs
duplicate yes/no token
duplicate active match ID
team name mismatch
team ID mismatch
league mismatch
series mismatch
scheduled_start_utc stale
yes token price contradicts yes-team net-worth state
MATCH_WINNER non-decider incorrectly treated as MAP_WINNER
```

Add quarantine writeback to `markets.yaml`:

```yaml
mapping_state: quarantined
quarantine_reason: orientation_flip_suspected
quarantined_at_utc: ...
quarantined_until: ...
```

`sync_markets.py` already respects quarantined mappings, so this fits existing code. 

### Acceptance criteria

If orientation flip is suspected once in real live mode, no further orders can be placed on that market until manually cleared.

---

## Phase 7 — Align entry and exit semantics per strategy

### Current repo reality

Exit logic is strategy-aware but split across `paper_trader.py` and `live_exit_engine.py`.

`live_exit_engine.py` treats `VALUE` as true hold-to-settle, exiting only at game-over, catastrophe salvage, fair invalidation, or max-hold. 

`DSWING` is different: it is not hold-to-series-settle. It exits at map-end convergence because non-decider match-winner tokens do not redeem at map end. 

Event reversal exits are currently quarantined unless explicitly enabled. 

### Exact implementation

Create:

```text
exit_policy.py
```

Move all strategy exit contracts there:

```python
class ExitPolicy:
    def decide(position, book, game, game_over_match_ids) -> ExitDecision:
        ...
```

Policies:

```text
VALUE_EDGE:
  hold_policy: thesis_invalidation
  exits: game_over, fair_invalidation, catastrophe_salvage, max_hold

EVENT_CONTINUATION_EDGE:
  hold_policy: thesis_invalidation or repricing_120s
  exits: depends on strategy YAML

EVENT_REVERSAL_EDGE:
  hold_policy: reversal_bounce_or_thesis
  exits: disabled unless EVENT_REVERSAL_ACTIVE_EXITS_ENABLED

DSWING:
  hold_policy: map_end_convergence
  exits: map_end_convergence, max_hold
```

Then make both `paper_trader.py` and `live_exit_engine.py` use the same `exit_policy.py`.

### Acceptance criteria

A paper position and a live position with the same strategy metadata must produce the same exit decision given the same book/game state.

---

## Phase 8 — Refactor `main.py` only after the policy layers exist

### Current repo problem

`main.py` imports and coordinates almost everything: feeds, mapping, events, value, decisive swing, allocation, paper, live, reconciliation, exits, discovery, logging, manual orders, enrichment, and markouts. 

Refactoring it before policy/config stabilization would create churn without reducing risk.

### Target structure

After Phases 1–7:

```text
runtime/
  bot_runtime.py
  feed_runtime.py
  mapping_runtime.py
  strategy_runtime.py
  execution_runtime.py
  markout_runtime.py
```

Keep `main.py` thin:

```python
from runtime.bot_runtime import BotRuntime
from runtime_config import load_config

def main():
    cfg = load_config()
    BotRuntime(cfg).run()
```

### Acceptance criteria

`main.py` should not contain trading logic. It should only wire components.

---

## Phase 9 — Tests that match the repo’s real risk

Do not start with broad “unit tests everywhere.” Start with failure modes that can lose money.

### Test group 1 — mapping orientation

Files:

```text
mapping_validator.py
sync_markets.py
value_engine.py
live_executor.py
```

Cases:

```text
normal side mapping
reversed side mapping
yes/no token swapped
strong lead + cheap favorite token = orientation_flip_suspected
stale scheduled market rejected
MATCH_WINNER non-decider rejected by VALUE
```

### Test group 2 — policy parity

Files:

```text
execution_policy.py
paper_trader.py
live_executor.py
```

Cases:

```text
paper research allows but logs live rejection
paper live_parity rejects
dry_live creates attempt but does not call CLOB
real_live requires fresh book and fresh Steam
hold-to-settle bypasses edge/lag but not book/mapping/freshness
```

### Test group 3 — strategy engines

Files:

```text
value_engine.py
event_triggered_value_engine.py
decisive_swing_engine.py
```

Cases:

```text
VALUE rejects ask below anti-flip floor
VALUE rejects huge edge as model/mapping suspect
EVENT_CONTINUATION requires fair_delta and reprice gap
EVENT_REVERSAL contract uses reversal_bounce_or_thesis
DSWING only works on MATCH_WINNER
DSWING requires series state
DSWING one-snipe-per-match-side-token-game
```

`DSWING` has an explicit `_sniped` set and persistent `logs/dswing_snipes.json`, so that behavior needs tests. 

### Test group 4 — exit parity

Files:

```text
paper_trader.py
live_exit_engine.py
exit_policy.py
```

Cases:

```text
VALUE holds through normal price movement
VALUE exits on game_over
VALUE catastrophe salvage requires cheap bid + backed side losing
DSWING exits on map_end_convergence
EVENT_REVERSAL active exits disabled by default
hold-to-settle ignores TP/SL/trailing/horizon
```

---

# Actual implementation order

## Step 1 — Add `runtime_config.py` and `check_config.py`

Do this before touching strategy logic.

Files changed:

```text
config.py
runtime_config.py
check_config.py
.env.example
README.md
```

## Step 2 — Add `execution_policy.py`

Initially call it from `live_executor.py` only. Do not modify every engine at once.

Files changed:

```text
execution_policy.py
live_executor.py
storage.py
```

## Step 3 — Add paper live-parity mode

Files changed:

```text
paper_trader.py
main.py
storage.py
analyze_logs.py
```

## Step 4 — Extract strategy contracts

Files changed:

```text
strategy_registry.py
strategies/*.yaml
event_triggered_value_engine.py
decisive_swing_engine.py
value_engine.py
```

## Step 5 — Add mapping audit/quarantine

Files changed:

```text
mapping_audit.py
mapping_quarantine.py
sync_markets.py
mapping_validator.py
main.py
```

## Step 6 — Add shared exit policy

Files changed:

```text
exit_policy.py
paper_trader.py
live_exit_engine.py
live_position_store.py
```

## Step 7 — Add SQLite dual-write state

Files changed:

```text
state_store.py
schema.sql
live_position_store.py
live_reconciliation.py
paper_trader.py
storage.py
```

## Step 8 — Refactor `main.py`

Only after the above stabilizes.

---

# What I would not do yet

I would **not** add new alpha signals yet. The repo already has enough strategy complexity.

I would **not** rewrite storage first. `storage.py` already has a CSV + optional batch-writer pattern, and live position JSON has reconciliation. Stabilize policy/config before changing persistence.

I would **not** remove legacy event logic immediately. Keep it as diagnostics until the new strategy registry proves stable.

I would **not** enable real live beyond the tiny guarded test until paper/live parity reporting shows positive results on the live-passable subset.

---

# Best next concrete task

Start with:

```text
runtime_config.py + check_config.py
```

because the repo’s current biggest immediate risk is not model quality. It is that runtime behavior depends on inconsistent defaults across `config.py`, `.env.example`, README commands, and live executor assumptions.

Once config is deterministic, implement:

```text
execution_policy.py
```

That will remove the second biggest risk: duplicated paper/live gates.
