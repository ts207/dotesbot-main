I re-inspected the actual repo before writing this. The final plan below is tailored to what the code currently does, not just the abstract strategy discussion.

# Executive conclusion

The repo should be refactored around this model:

```text
TopLive snapshot changes
→ detect primitive Dota state transitions
→ recompute fair win probability
→ compare fair probability to executable Polymarket ask
→ buy only if market underreacted
→ hold to settlement unless thesis breaks
```

The bot should **not** trade `POLL_*` labels as if they are real Dota events.

The current repo has already partially discovered this. `config.py` says the robust edge is net-worth-implied fair probability minus market price, and it requires backed-side net-worth lead, calibrated fair edge, and a max entry price for hold-to-settle events.  The `value_engine.py` is already the cleanest implementation of that idea: it uses TopLive only, validates game state, gates market type, checks book freshness, computes fair with `winprob`, and trades fair minus ask.  

So the final direction is:

```text
Keep VALUE as the core strategy.
Replace the legacy event strategy with EVENT_TRIGGERED_VALUE.
Move current event labels into diagnostics/research.
```

---

# Current repo state

## 1. The repo says it is event-driven, but runtime is not

The README describes the project as a Steam Dota 2 → Polymarket bot that monitors game state and order books, paper by default, with optional guarded live execution. It also says `logs/dota_events.csv` records meaningful Dota state changes and `logs/signals.csv` records signal/skip decisions. 

But `main.py` currently says the paper runtime has two entry strategies: `VALUE` and `DSWING`. It explicitly says event detection remains enabled for diagnostics and sets `ENABLE_EVENT_ENTRY_STRATEGY = False`. 

This is the main concept drift:

```text
Docs/framework: event trading
Runtime reality: value trading + dswing, events diagnostic
```

That should be made explicit in code and logs.

---

## 2. The current event detector is not a primitive event detector

`event_detector.py` defines a `DotaEvent` dataclass with event type, direction, pressure scores, confidence, cadence metadata, structure metadata, and many trading-oriented fields. It also has a large `TACTICAL_PRIORITY` table containing `POLL_*`, `OBJECTIVE_CONVERSION_*`, and base-pressure events. 

That means the current event detector is doing too much:

```text
raw state transition detection
+ tactical interpretation
+ trading confidence
+ backtest assumptions
+ priority arbitration
```

Those need to become separate layers.

---

## 3. The taxonomy confirms the event layer is strategy-heavy

`event_taxonomy.py` classifies events into Tier A, Tier B, research, retired, unreachable pro events, blocking events, and event families. Many comments are backtest/trade-performance notes, not game-state definitions. 

This is useful for research, but it is not a clean “actual Dota event” taxonomy.

For example:

```text
POLL_VALUE_DISAGREEMENT
POLL_PHASE_NORMALIZED_LEAD
POLL_RAPID_STOMP
POLL_DECISIVE_STOMP
POLL_FIRST_SWING_SETTLE
```

These are not primitive Dota events. They are derived state or strategy labels.

---

## 4. The signal engine already admits the real edge is value, not the detector

`signal_engine.py` has an `ACTIVE_EVENTS` table with event specs, historical comments, and expected move assumptions. But later in the same engine, the S3 gate says the edge for hold-to-settle events is net-worth-predicts-winner, not the detector; it requires backed-side lead, calibrated fair value above executable price, and max entry price.  

That is the correct thesis. The detector should only wake the bot up. It should not be the source of expected value.

---

## 5. The current winner event set is too abstract

The signal engine’s current winner whitelist is:

```python
WINNER_TRADE_EVENTS = {
    "POLL_FIRST_SWING_SETTLE",
    "POLL_PHASE_NORMALIZED_LEAD",
    "POLL_VALUE_DISAGREEMENT",
    "POLL_RAPID_STOMP",
    "POLL_DECISIVE_STOMP",
}
```

The code comments say these were chosen from backtests and that excluded events backtested net-negative. 

But these are still not “real Dota events.” They are better understood as:

| Current event                | Real classification             |
| ---------------------------- | ------------------------------- |
| `POLL_FIRST_SWING_SETTLE`    | strategy trigger                |
| `POLL_PHASE_NORMALIZED_LEAD` | derived state                   |
| `POLL_VALUE_DISAGREEMENT`    | misnamed soft lead/value signal |
| `POLL_RAPID_STOMP`           | net-worth swing state           |
| `POLL_DECISIVE_STOMP`        | dominant lead state             |

Keep their research value, but stop treating them as primitive event types.

---

## 6. The value engine is the core to build on

`value_engine.py` already does most of the correct work:

It only processes `top_live` updates, validates TopLive state, skips game-over, gates game time, requires a minimum net-worth lead, handles map-winner vs match-winner market scope, resolves YES/NO by `steam_side_mapping`, checks book timestamp and age, rejects too-high and too-low prices, has an orientation-flip guard, computes fair with `winprob`, and requires edge above threshold.  

So the new event strategy should be an extension of `ValueEngine`, not an extension of legacy `EventSignalEngine`.

---

## 7. The exit system is already mostly correct for value trades

`live_exit_engine.py` treats `trader_kind == "value"` as true hold-to-settlement. It exits on game over, max-hold timeout, or catastrophe salvage when bid collapses and net worth confirms the backed side is losing. 

This is directionally right.

But it should add one missing idea:

```text
fair thesis invalidation
```

Not a normal take-profit. Not a trailing stop. Not generic stop-loss. Just:

```text
if current fair has fallen below entry thesis and market still offers a better exit, sell
```

---

# Final target architecture

## Layer 1 — primitive actual Dota events

Create a new module:

```text
actual_dota_event_detector.py
```

Add a clean enum:

```python
class ActualDotaEventType(str, Enum):
    TEAM_KILL_SCORE_CHANGE = "TEAM_KILL_SCORE_CHANGE"
    MULTI_KILL_WINDOW = "MULTI_KILL_WINDOW"
    NETWORTH_LEAD_CHANGE = "NETWORTH_LEAD_CHANGE"
    NETWORTH_SWING_WINDOW = "NETWORTH_SWING_WINDOW"
    NETWORTH_LEAD_FLIP = "NETWORTH_LEAD_FLIP"
    TOWER_DESTROYED = "TOWER_DESTROYED"
    TOWER_TIER_CLEARED = "TOWER_TIER_CLEARED"
    GAME_ENDED = "GAME_ENDED"
```

These should be factual, TopLive-derived state transitions.

Do **not** include these as fast events:

```python
NOT_FAST_EVENTS = {
    "ROSHAN_KILLED",
    "AEGIS_PICKED_UP",
    "AEGIS_HELD_BY_TEAM",
    "BUYBACK_USED",
    "HERO_DEATH",
    "HERO_RESPAWN",
    "TEAM_WIPE",
}
```

Reason: TopLive does not provide enough reliable fast state for those. `steam_client.py` normalizes TopLive into match ID, teams, game time, Radiant lead, scores, building/tower state, server ID, delay, game-over/deactivate time, timestamps, and raw data. It does not normalize Roshan/Aegis. 

`GetRealtimeStats` can find Aegis from item ID `117`, but that module describes it as delayed rich context and should not overwrite fast fields. 

---

## Layer 2 — derived game state

Create:

```text
derived_game_state.py
```

This should compute features, not events:

```python
class DerivedGameStateType(str, Enum):
    PHASE_ADJUSTED_NETWORTH_LEAD = "PHASE_ADJUSTED_NETWORTH_LEAD"
    DOMINANT_NETWORTH_LEAD = "DOMINANT_NETWORTH_LEAD"
    STRUCTURE_ADVANTAGE = "STRUCTURE_ADVANTAGE"
    KILL_NETWORTH_ALIGNMENT = "KILL_NETWORTH_ALIGNMENT"
    KILL_NETWORTH_DIVERGENCE = "KILL_NETWORTH_DIVERGENCE"
    PUSH_SETUP_STATE = "PUSH_SETUP_STATE"
```

These replace the conceptual use of many `POLL_*` labels.

Examples:

```text
POLL_PHASE_NORMALIZED_LEAD → PHASE_ADJUSTED_NETWORTH_LEAD
POLL_STRUCTURAL_DOMINANCE → STRUCTURE_NW_KILL_ALIGNMENT
POLL_PRE_PUSH_SETUP → PUSH_SETUP_STATE
POLL_VALUE_DISAGREEMENT → SOFT_LEAD_VALUE_CONTEXT
```

The current `structure_state.py` is useful here because it decodes tower state and rejects invalid structure deltas, low-confidence state, schema changes, impossible structure increases, and impossible T4 changes. 

---

## Layer 3 — trading signals

Create:

```text
event_triggered_value_engine.py
```

This should not use `EventSignalEngine`’s probability-shock table.

It should use the same philosophy as `ValueEngine`.

```python
class TradingSignalType(str, Enum):
    VALUE_EDGE = "VALUE_EDGE"
    EVENT_TRIGGERED_VALUE_EDGE = "EVENT_TRIGGERED_VALUE_EDGE"
    FIRST_SWING_VALUE_EDGE = "FIRST_SWING_VALUE_EDGE"
    UNDERDOG_REVERSAL_EDGE = "UNDERDOG_REVERSAL_EDGE"
    EXIT_RISK_SIGNAL = "EXIT_RISK_SIGNAL"
```

Primary new strategy:

```text
EVENT_TRIGGERED_VALUE_EDGE
```

Definition:

```text
A primitive fast Dota event occurred,
the fair probability changed materially,
and executable market price did not adjust enough.
```

---

# Final trading logic

## Step 1 — receive TopLive snapshot

Use TopLive for entries.

Do not use delayed `GetRealtimeStats` to open trades.

Delayed context can:

```text
annotate logs
veto a trade
reduce size
inform an exit
support post-trade research
```

It must not:

```text
create a buy signal
```

---

## Step 2 — detect primitive events

For each match, diff the previous TopLive snapshot against the current one.

Emit events like:

```json
{
  "event_type": "NETWORTH_SWING_WINDOW",
  "side": "radiant",
  "networth_delta": 3200,
  "window_sec": 38,
  "radiant_lead_before": 1100,
  "radiant_lead_after": 4300,
  "game_time_sec": 1260,
  "source": "top_live"
}
```

Keep this factual. No `POLL_*` names here.

---

## Step 3 — recompute fair probability

Before and after event:

```python
fair_before = winprob.fair(...)
fair_after = winprob.fair(...)
fair_delta = fair_after - fair_before
```

The bot should care about fair-probability movement, not the event label.

Minimum suggested gate:

```python
MIN_FAIR_DELTA = 0.06
```

---

## Step 4 — compare fair to executable ask

For the backed side:

```python
edge = fair_after - executable_ask
```

Suggested first thresholds:

```python
EVENT_VALUE_MIN_EDGE = 0.10
EVENT_VALUE_MIN_FAIR_DELTA = 0.06
EVENT_VALUE_MAX_ASK = 0.84
EVENT_VALUE_MIN_ASK = 0.50
EVENT_VALUE_MAX_EDGE = 0.30
```

The `VALUE_MIN_PRICE=0.50` and `VALUE_MAX_EDGE=0.30` concepts already exist in `value_engine.py` to avoid cheap-token orientation flips and fake huge edges. 

---

## Step 5 — execute only if all gates pass

```python
def should_enter_event_value(signal):
    return (
        signal.source == "top_live"
        and signal.event_type in FAST_DOTA_EVENTS
        and signal.mapping_confident
        and signal.book_fresh
        and signal.source_fresh
        and signal.fair_delta_abs >= EVENT_VALUE_MIN_FAIR_DELTA
        and signal.edge >= EVENT_VALUE_MIN_EDGE
        and EVENT_VALUE_MIN_ASK <= signal.ask <= EVENT_VALUE_MAX_ASK
        and signal.edge <= EVENT_VALUE_MAX_EDGE
        and not signal.already_holding_same_token
        and not signal.opposing_position_forbidden
        and not signal.cooldown_active
    )
```

---

# Event-specific recommendations

## `TEAM_KILL_SCORE_CHANGE`

Use as a wake-up trigger only.

Do not trade from this alone.

Require one of:

```text
same-side net-worth movement
fair delta ≥ 0.06
lead flip
market underreaction
```

## `MULTI_KILL_WINDOW`

Use when a side gains multiple kills in a short window.

Suggested gate:

```python
kills_delta >= 3
opponent_kills_delta <= 1
same_side_networth_confirmation = True
fair_delta >= 0.08
edge >= 0.10
```

Do not call this `TEAM_WIPE`. TopLive score deltas cannot prove a full team wipe.

## `NETWORTH_LEAD_CHANGE`

Log every meaningful change, but do not trade all of them.

It is the raw input to fair probability.

## `NETWORTH_SWING_WINDOW`

This should be the main event-triggered value signal.

Suggested swing thresholds:

```python
if 10 <= game_time_min <= 20:
    min_swing = 1800
elif 20 < game_time_min <= 35:
    min_swing = 2800
else:
    min_swing = 4000
```

Trade only when fair and market edge confirm.

## `NETWORTH_LEAD_FLIP`

Powerful but noisy.

Require:

```python
min_abs_before = 1000
min_abs_after = 1000
min_total_swing = 2500
fair_delta >= 0.08
edge >= 0.10
```

## `TOWER_DESTROYED`

Confirmation, not primary entry.

Trade only if it supports a value edge.

## `TOWER_TIER_CLEARED`

Context for the fair model.

Not standalone entry.

## `GAME_ENDED`

Settlement/reconciliation only.

Never an entry.

---

# What to do with old event engine

Do not delete it immediately.

Reclassify it:

```text
legacy_event_detector.py
legacy_signal_engine.py
```

or keep filenames and add explicit namespace fields:

```text
event_namespace = "legacy_strategy_label"
```

Then update logs:

```text
actual_dota_events.csv      → primitive facts only
legacy_dota_events.csv      → old POLL_* diagnostics
strategy_signals.csv        → market-aware buy/skip decisions
```

The current `logs/dota_events.csv` should stop mixing primitive game facts with trading labels.

---

# Exit policy

## Keep hold-to-settlement for value trades

Do not create a generic take-profit or trailing-stop exit for the main value strategy.

The current repo already learned this. `live_exit_engine.py` says active exits underperformed hold-to-settlement for value trades and therefore exits value positions only on settlement, max-hold safety, or catastrophe salvage. 

Keep that.

## Add thesis invalidation

Add one defensive exit:

```python
def fair_invalidation_exit(position, game, book):
    current_fair = compute_current_fair(position.backed_direction, game)
    bid = book.best_bid

    if bid is None:
        return False

    if current_fair < position.entry_price - 0.03 and current_fair < bid - 0.05:
        return True

    return False
```

This exits only when:

```text
the model now says the position is no longer worth holding,
and the market still offers a better exit than the new fair value.
```

This prevents blindly riding obvious losers to zero without turning the bot into a scalper.

---

# Position model changes

Extend `LivePosition` and paper positions with:

```python
strategy_kind: str
hold_policy: str
entry_fair: float
entry_edge: float
entry_backed_side: str
entry_radiant_lead: int
entry_game_time_sec: int
entry_actual_event_type: str | None
entry_derived_state_flags: list[str]
```

Recommended hold policies:

```python
class HoldPolicy(str, Enum):
    SETTLEMENT = "settlement"
    THESIS_INVALIDATION = "thesis_invalidation"
    TIMED_SCALP = "timed_scalp"
    UNDERDOG_BOUNCE = "underdog_bounce"
    ARB_SETTLEMENT = "arb_settlement"
```

Use:

```python
hold_policy = "thesis_invalidation"
```

for:

```text
VALUE_EDGE
EVENT_TRIGGERED_VALUE_EDGE
FIRST_SWING_VALUE_EDGE
```

Use active exits only for:

```text
UNDERDOG_REVERSAL_EDGE
TIMED_SCALP
manual override trades
```

---

# Patch sequence tailored to this repo

## PR 1 — add primitive actual event detector

Create:

```text
actual_dota_event_detector.py
actual_dota_event_types.py
```

Implement:

```text
TEAM_KILL_SCORE_CHANGE
MULTI_KILL_WINDOW
NETWORTH_LEAD_CHANGE
NETWORTH_SWING_WINDOW
NETWORTH_LEAD_FLIP
TOWER_DESTROYED
TOWER_TIER_CLEARED
GAME_ENDED
```

Inputs:

```text
previous TopLive snapshot
current TopLive snapshot
mapping
structure_state decoder
```

Outputs:

```python
ActualDotaEvent(
    event_type=...,
    side=...,
    match_id=...,
    game_time_sec=...,
    previous_value=...,
    current_value=...,
    delta=...,
    window_sec=...,
    source="top_live",
)
```

Do not trade yet.

---

## PR 2 — split logs

Add:

```text
ACTUAL_DOTA_EVENTS_CSV_PATH=logs/actual_dota_events.csv
LEGACY_DOTA_EVENTS_CSV_PATH=logs/legacy_dota_events.csv
STRATEGY_SIGNALS_CSV_PATH=logs/strategy_signals.csv
```

Keep current `DotaEventLogger` for old diagnostics if needed, but do not let `dota_events.csv` imply that `POLL_*` labels are real events.

---

## PR 3 — isolate legacy event engine

In `main.py`, rename comments and variables around the existing event detector:

```text
EventDetector → LegacyTacticalEventDetector
EventSignalEngine → LegacyEventSignalEngine
```

or at least add:

```python
signal["event_namespace"] = "legacy_strategy_label"
```

Keep `ENABLE_EVENT_ENTRY_STRATEGY = False` unless deliberately testing legacy behavior. 

---

## PR 4 — implement event-triggered value engine

Create:

```text
event_triggered_value_engine.py
```

Core type:

```python
@dataclass(frozen=True)
class EventTriggeredValueSignal:
    signal_id: str
    match_id: str
    actual_event_type: str
    direction: str
    side: str
    token_id: str
    fair_before: float
    fair_after: float
    fair_delta: float
    ask: float
    edge: float
    lead: int
    game_time_sec: int
    book_age_ms: int
    reason: str
```

This engine should call the same `winprob.fair` path as `ValueEngine`, not `EventSignalEngine.apply_probability_move`.

---

## PR 5 — wire it into `main.py`

In the active game loop, after TopLive snapshot processing and before/alongside `ValueEngine`:

```python
actual_events = actual_event_detector.observe(game, mapping)
actual_event_logger.log(actual_events)

for event in actual_events:
    signals = event_triggered_value_engine.evaluate(
        event=event,
        game=game,
        mapping=mapping,
        book_store=book_store,
        entered_tokens=_entered_toks,
    )
```

Then route `EventTriggeredValueSignal` through the same live/paper path used by `ValueSignal`.

Set:

```python
event_type = "EVENT_TRIGGERED_VALUE"
trader_kind = "value"
hold_policy = "thesis_invalidation"
```

This allows the existing value hold-to-settlement logic to remain valid.

---

## PR 6 — remove unsupported fast Roshan/Aegis entries

Remove `POLL_AEGIS_MOMENTUM` from any entry path.

Keep delayed Aegis only for:

```text
logs
underdog risk annotation
possible veto
possible exit context
```

The current code already uses delayed Aegis for underdog adverse exits in `main.py`; that should remain defensive only, not an entry signal. 

---

## PR 7 — add fair thesis invalidation exit

Modify `live_exit_engine.py` for `trader_kind == "value"`:

Current behavior:

```text
game_over
catastrophe_salvage
max_hold_timeout
otherwise hold
```

Add optional:

```text
fair_invalidation
```

Config:

```python
VALUE_EXIT_FAIR_INVALIDATION_ENABLED=true
VALUE_EXIT_FAIR_ENTRY_BUFFER=0.03
VALUE_EXIT_FAIR_BID_BUFFER=0.05
```

Rule:

```python
if current_fair < entry_price - 0.03 and current_fair < bid - 0.05:
    exit("fair_invalidation")
```

Keep normal take-profit/trailing-stop disabled for `value`.

---

## PR 8 — tighten paper/live parity

`signal_engine.py` currently bypasses some stale-data checks in paper mode but applies them in real-live mode.  That is acceptable for research, but paper logs must label this clearly.

Add to every signal:

```text
would_pass_live_gates=true/false
live_skip_reason=...
paper_only_bypass=true/false
```

This prevents overestimating live-executable edge.

---

## PR 9 — validation suite

Before live use, add tests for:

```text
TEAM_KILL_SCORE_CHANGE
MULTI_KILL_WINDOW
NETWORTH_LEAD_CHANGE
NETWORTH_SWING_WINDOW
NETWORTH_LEAD_FLIP
TOWER_DESTROYED
TOWER_TIER_CLEARED
GAME_ENDED
TopLive-only entry enforcement
delayed Aegis cannot open trades
Roshan cannot open trades
mapping orientation flip guard
fair invalidation exit
catastrophe salvage exit
```

The README says `pytest` is expected, so this should become enforceable. 

---

# Final recommended runtime

Run three active systems:

## 1. `VALUE_EDGE`

Already exists.

Keep it as the primary strategy.

```text
Every TopLive tick:
compute fair
compare fair to executable ask
buy if edge passes
hold to settlement
```

## 2. `EVENT_TRIGGERED_VALUE_EDGE`

New.

```text
Only when primitive actual event fires:
compute fair_before/fair_after
require material fair_delta
buy if executable ask underreacted
hold to settlement unless thesis breaks
```

## 3. `THESIS_BASED_EXIT`

Modified existing exit engine.

```text
game_over
max_hold_timeout
catastrophe_salvage
fair_invalidation
```

---

# Deployment order

## Stage A — no trading changes

Add actual event logging only.

Success condition:

```text
actual_dota_events.csv is clean
no POLL_* labels inside it
events match raw TopLive state changes
```

## Stage B — paper-only event-triggered value

Enable:

```text
EVENT_TRIGGERED_VALUE_ENABLED=true
ENABLE_REAL_LIVE_TRADING=false
```

Success condition:

```text
positive settlement EV
positive live-gate-adjusted EV
skip reasons explain most rejects
no delayed-data entries
no unsupported Roshan/Aegis entries
```

## Stage C — tiny guarded live path

Use existing small-live constraints:

```text
MAX_TRADE_USD=1
MAX_OPEN_POSITIONS=1
ENABLE_REAL_LIVE_TRADING=false first
then true only after clean dry run
```

The README already frames live as a guarded tiny order-flow test, not an unattended profit bot. 

## Stage D — scale only after reconciliation and exits are proven

Required before scaling:

```text
startup live reconciliation
atomic position writes
mapping confidence startup report
fair invalidation exit dry-run logs
catastrophe salvage logs
live/paper gate parity report
```

---

# Final definition of done

This refactor is done when:

```text
1. actual_dota_events.csv contains only primitive factual events.
2. legacy POLL_* events are diagnostics/research only.
3. VALUE_EDGE remains the primary strategy.
4. EVENT_TRIGGERED_VALUE_EDGE is implemented as a wrapper around fair-value logic.
5. Delayed data cannot open trades.
6. Roshan/Aegis cannot open fast trades without a verified fast source.
7. Value trades hold to settlement by default.
8. Thesis invalidation exists as a defensive exit.
9. Paper logs show live-gate pass/fail separately from paper-only results.
10. Live mode fails closed on stale source, stale book, missing model, ambiguous mapping, unsupported market type, or suspected orientation flip.
```

# Final operating rule

```text
Do not trade event labels.
Trade mispriced win probability after real TopLive game-state changes.
```

That is the clean final plan for this repo.
