Below is the full repo-grounded plan from the **current inspected state**, not the earlier baseline.

# Current repo status

The project is partially through the cleanup plan. Some good changes have landed:

```text
VALUE now has model_available / fair_raw / fair_used fields.
EVENT now has edge_type, target_horizon, fair_delta, market_reprice, and event_reprice_gap fields.
DSWING now has edge_type / target_horizon metadata.
Allocator candidates now carry edge_type and target_horizon.
StrategySignalLogger now logs many event-specific fields.
```

But the repo still has several blockers:

```text
P0: main.py still imports and can run dota_fair_model / ML_ARBITRAGE.
P0: handoff_fix.patch is still tracked.
P1: fair_value.py still lacks conservative phase shrink.
P1: _lead_slope(record_history=False) still mutates/prunes history.
P1: DSWING still ignores model_available.
P1: live_exit_engine still ignores model_available for current fair.
P2: allocator still does not log uncontested winners.
P2: no unified candidate-level audit ledger yet.
P2: event market-reprice fields exist, but pre-event book capture is not guaranteed.
```

The most important repo facts: `main.py` still imports `load_bundle` and `build_feature_row` from `dota_fair_model`; the startup path can still load the model; and the tick-level block can still create `ML_ARBITRAGE` paper entries.   

---

# Final project objective

Build a **TopLive-only, auditable Dota trading bot** with four clearly separated strategy books:

```text
VALUE_EDGE:
  state mispricing → settlement

EVENT_CONTINUATION_EDGE:
  event repricing → 30/60/120s markout and/or proven settlement edge

EVENT_REVERSAL_EDGE:
  event overreaction / bounce → short-horizon bounce capture

DSWING:
  BO3 match-winner map-end convergence → exit_bid - entry_price
```

The bot should not become a broad ML prediction system. It should become a **selective mispricing-capture system** that trades only when:

```text
fresh TopLive state
+ valid market mapping
+ conservative fair
+ executable book price
+ strategy-specific thesis
+ strategy-specific exit
+ logged evidence
= positive expected value by bucket
```

---

# Phase 0 — repo hygiene

## Goal

Remove artifacts and make the repo clean before functional changes.

## Patch

Delete:

```text
handoff_fix.patch
```

It is still tracked in the repo. 

## Acceptance

```bash
git rm handoff_fix.patch
pytest
```

Commit:

```text
Remove accidental patch artifact
```

---

# Phase 1 — remove ML runtime contamination

## Goal

The runtime should not import, load, or trade from `dota_fair_model`.

## Current problem

`main.py` still imports:

```python
from dota_fair_model.inference import load_bundle
from dota_fair_model.features import build_feature_row
```



It still conditionally loads `DOTA_FAIR_MODEL_PATH` when `ML_STRATEGY_ENABLED` is true. 

It still runs a tick-level ML block that builds feature rows, predicts fair, updates paper fair values, and can create `ML_ARBITRAGE` paper entries.   

## Patch

In `main.py`:

```text
Remove dota_fair_model imports.
Remove load_bundle startup model loading.
Remove model_bundle from steam_loop.
Remove team_stats loading from dota_fair_model/models/team_stats.json.
Delete the tick-level ML block.
Delete ML_ARBITRAGE entry path.
Keep ML_STRATEGY_ENABLED only as deprecated/ignored config if needed.
```

In `config.py`, keep this only as compatibility:

```python
# Deprecated. ML live/paper entries are disabled in the TopLive-only stack.
ML_STRATEGY_ENABLED = os.getenv("ML_STRATEGY_ENABLED", "false").lower() in {"1", "true", "yes"}
```

## Tests

Add `tests/test_no_ml_entry.py`:

```python
from pathlib import Path

def test_main_has_no_dota_fair_model_import():
    text = Path("main.py").read_text()
    assert "from dota_fair_model" not in text
    assert "import dota_fair_model" not in text

def test_ml_arbitrage_path_removed():
    text = Path("main.py").read_text()
    assert "ML_ARBITRAGE" not in text
    assert "build_feature_row" not in text
    assert "load_bundle" not in text
```

## Acceptance

```bash
python -m py_compile main.py
pytest tests/test_no_ml_entry.py
```

Commit:

```text
Remove ML runtime trading path
```

---

# Phase 2 — finish `fair_value.py`

## Goal

Make `fair_value.py` the single safe fair gateway.

## Current state

`FairValueResult` now includes `fair_raw`, `fair_used`, `model_available`, and `model_reason`. 

`compute_side_fair()` now rejects missing lead, invalid lead, missing game time, and invalid game time.   

But `_lead_slope()` still uses `setdefault()` and prunes the deque even with `record_history=False`. 

Also, `fair_raw` and `fair_used` are currently identical; no conservative shrink is applied. 

## Patch

Extend `FairValueResult`:

```python
@dataclass(frozen=True)
class FairValueResult:
    side: str
    fair: float
    elo_diff: float | None
    lead_slope: float | None = None
    draft_h2h: float | None = None
    fair_source: str = "winprob"

    fair_raw: float | None = None
    fair_used: float | None = None
    model_available: bool = True
    model_reason: str = "ok"

    phase_shrink: float = 1.0
    confidence_multiplier: float = 1.0
    radiant_lead: int | None = None
    game_time_sec: int | None = None
    slope_available: bool = True
```

Fix `_lead_slope()`:

```python
def _lead_slope(match_id: str, radiant_lead: int, now_ns: int, record_history: bool = True) -> float | None:
    if record_history:
        dq = _lead_hist.setdefault(match_id, deque(maxlen=4000))
        if not dq or dq[-1][0] != now_ns:
            dq.append((now_ns, int(radiant_lead)))

        cutoff = now_ns - int(_SLOPE_WINDOW_NS * 1.5)
        while dq and dq[0][0] < cutoff:
            dq.popleft()
    else:
        dq = _lead_hist.get(match_id)
        if not dq:
            return None

    target = now_ns - _SLOPE_WINDOW_NS
    past = None
    for ns, ld in dq:
        if ns <= target:
            past = ld
        else:
            break

    return None if past is None else float(radiant_lead - past)
```

Add phase shrink:

```python
def _phase_shrink(game_time_sec: int) -> float:
    minute = game_time_sec / 60.0
    if minute < 25:
        return 1.00
    if minute < 30:
        return 0.92
    if minute < 35:
        return 0.85
    if minute < 45:
        return 0.75
    return 0.65
```

Use conservative fair:

```python
fair_raw = fair_leader if side_lead >= 0 else 1.0 - fair_leader
phase = _phase_shrink(game_time)
confidence = phase
fair_used = 0.5 + (fair_raw - 0.5) * confidence
fair_used = max(0.0, min(1.0, fair_used))
```

Return:

```python
return FairValueResult(
    side=side,
    fair=fair_used,
    fair_raw=fair_raw,
    fair_used=fair_used,
    elo_diff=elo_diff,
    lead_slope=lead_slope,
    draft_h2h=draft,
    fair_source="winprob",
    model_available=True,
    model_reason="ok",
    phase_shrink=phase,
    confidence_multiplier=confidence,
    radiant_lead=radiant_lead,
    game_time_sec=game_time,
    slope_available=slope_available,
)
```

## Tests

Add `tests/test_fair_value.py`:

```text
missing radiant_lead returns model_available=False
invalid radiant_lead returns model_available=False
missing game_time returns model_available=False
invalid game_time returns model_available=False
record_history=False does not create a new _lead_hist key
record_history=False does not prune existing history
fair_used is closer to 0.5 than fair_raw late game
fair == fair_used when available
fair == 0.5 when unavailable
```

Commit:

```text
Harden fair value availability and conservative fair
```

---

# Phase 3 — finish availability propagation

## Goal

No strategy trades unavailable fair, and unavailable current fair never forces an exit.

## Current status

VALUE already checks `fair_res.model_available`. 

EVENT already checks availability for before/after fair. 

DSWING still does not check it:

```python
fair_res = compute_side_fair(game=game, side=direction)
p_game = fair_res.fair
```



`live_exit_engine._current_fair_for_position()` still returns `fair_res.fair` directly. 

## Patch

In `decisive_swing_engine.py`:

```python
fair_res = compute_side_fair(game=game, side=direction)
if not fair_res.model_available:
    return [DSwingReject(match_id, f"model_unavailable:{fair_res.model_reason}")]

p_game = fair_res.fair_used if fair_res.fair_used is not None else fair_res.fair
```

In `live_exit_engine.py`:

```python
fair_res = compute_side_fair(game=game, side=backed, record_history=False)
if not fair_res.model_available:
    return None
return fair_res.fair_used if fair_res.fair_used is not None else fair_res.fair
```

That makes fair invalidation safe because current fair unavailable becomes “no fair invalidation this tick,” not “exit.”

## Tests

Add:

```text
DSWING rejects unavailable model fair.
live_exit_engine does not fair-invalidate when current fair unavailable.
```

Commit:

```text
Reject unavailable fair in DSWING and exits
```

---

# Phase 4 — update allocator audit

## Goal

Log all allocator decisions, including uncontested winners.

## Current issue

`decision_to_log_row()` still returns `None` for uncontested winners. 

`AllocatorLogger` also says uncontested wins are not logged.  

The allocator candidate model now has useful edge/horizon fields, which should be logged for all winners. 

## Patch

Change:

```python
def decision_to_log_row(decision: AllocationDecision, *, include_uncontested: bool = True) -> dict | None:
    if not include_uncontested and not decision.blocked and decision.block_reason != "already_entered":
        return None
```

Add fields:

```text
candidate_count
blocked_count
allocator_winner
```

Update `AllocatorLogger` docstring:

```text
Logs all allocation decisions, including uncontested winners.
```

Update tests. Current tests expect no log row for uncontested winners, so they must change. 

Commit:

```text
Log uncontested allocator winners
```

---

# Phase 5 — preserve event/value separation

## Goal

Keep VALUE and EVENT as separate strategy books.

## Current good state

EVENT now has its own contract:

```text
continuation = event_repricing, repricing_120s
reversal = event_overreaction_bounce, bounce_120s
```



EVENT computes:

```text
fair_delta
market_reprice
remaining_event_edge
event_reprice_gap
```



## Remaining gap

`market_price_before_event` is taken from optional fields in the current book object:

```text
market_price_before_event
best_ask_before_event
pre_event_ask
pre_event_price
```



If those are not populated upstream, `market_reprice` and `event_reprice_gap` will be `None`.

## Patch

Add a real pre-event book snapshot at event-detection time:

```text
pre_event_bid
pre_event_ask
pre_event_mid
pre_event_book_received_at_ns
pre_event_book_age_ms
```

Pass those into `EventTriggeredValueEngine`.

Then compute:

```text
market_reprice = ask_after_event - ask_before_event
event_reprice_gap = fair_delta_used - market_reprice
```

For EVENT_CONTINUATION, primary metric:

```text
bid_120s - entry_ask
```

For EVENT_REVERSAL, primary metric:

```text
max_bid_120s - entry_ask
```

Commit:

```text
Capture pre-event book state for event repricing
```

---

# Phase 6 — add unified candidate audit

## Goal

Create one alpha ledger that joins candidate → allocator → execution → exit.

## Current status

`StrategySignalLogger` is much better now and logs event-specific fair/reprice fields.  

But logs are still fragmented:

```text
VALUE logs separately.
EVENT logs separately.
DSWING logs separately.
Allocator logs separately.
Execution logs separately.
Exit logs separately.
```

## Patch

Add `CandidateAuditLogger` or `FairModelAuditLogger`.

Required columns:

```text
timestamp_utc
received_at_ns
run_id
code_version
config_hash
candidate_set_id
signal_id
event_id

strategy_kind
strategy_family
strategy_subtype
edge_type
target_horizon

match_id
token_id
side
direction
market_type

game_time_sec
radiant_lead
lead_slope
slope_available
elo_diff
draft_h2h

fair_source
model_available
model_reason
fair_raw
fair_used
phase_shrink
confidence_multiplier

fair_before_raw
fair_before_used
fair_after_raw
fair_after_used
fair_delta_raw
fair_delta_used

market_price_before_event
market_price_after_event
market_reprice
remaining_event_edge
event_reprice_gap

bid
ask
spread
book_age_ms
edge_raw
edge_used

would_trade
reject_reason

allocator_winner
allocator_block_reason
blocked_strategies
execution_path

entered
entry_price
exit_price
exit_reason
pnl
roi
```

Commit:

```text
Add unified candidate audit logger
```

---

# Phase 7 — reports

## Goal

Turn logs into decisions.

Add scripts:

```text
scripts/fair_calibration_report.py
scripts/value_bucket_report.py
scripts/event_markout_report.py
scripts/event_incrementality_report.py
scripts/dswing_exit_quality_report.py
scripts/execution_quality_report.py
scripts/allocator_counterfactual_report.py
```

## VALUE report

Answer:

```text
Is fair_used calibrated?
Does fair_used - ask predict settlement ROI?
Which game-time / lead / ask / edge buckets work?
```

## EVENT report

Answer:

```text
Does fair_delta produce 30/60/120s markout?
Did market_reprice already consume the event?
Does EVENT_CONTINUATION beat comparable VALUE candidates?
Does EVENT_REVERSAL produce realizable bounce?
```

## DSWING report

Answer:

```text
Does series_fair - ask predict exit_bid - entry_price?
How much edge is lost after map-end detection?
Which game/series-score buckets work?
```

## Execution report

Answer:

```text
signal ask vs fresh ask vs fill price
edge_at_signal vs edge_at_fill
fill rate
slippage
price-cap rejects
book staleness
```

Commit:

```text
Add calibration and strategy reports
```

---

# Phase 8 — policy config and caps

## Goal

Move proven buckets into an explicit policy file.

Add:

```text
strategy_policy.yaml
```

Example:

```yaml
VALUE_EDGE:
  enabled: true
  edge_type: absolute_state_value
  target_horizon: settlement
  ask_min: 0.50
  ask_max: 0.84
  max_edge: 0.25
  min_edge_by_game_time:
    "10-20m": 0.10
    "20-25m": 0.12
    "25-30m": 0.16
    "30m+": null

EVENT_CONTINUATION_EDGE:
  enabled: true
  edge_type: event_repricing
  target_horizon: repricing_120s
  min_fair_delta: 0.06
  min_remaining_event_edge: 0.10
  min_event_reprice_gap: 0.08
  require_incremental_over_value: true

EVENT_REVERSAL_EDGE:
  enabled: false
  audit_only: true
  edge_type: event_overreaction_bounce
  target_horizon: bounce_120s

DSWING:
  enabled: true
  edge_type: map_end_series_convergence
  target_horizon: map_end
  min_edge: 0.06
  min_p_game: 0.90
```

Add caps:

```text
MAX_VALUE_OPEN_USD
MAX_EVENT_CONTINUATION_OPEN_USD
MAX_EVENT_REVERSAL_OPEN_USD
MAX_DSWING_OPEN_USD
MAX_OPEN_USD_PER_MATCH
MAX_DAILY_DRAWDOWN_USD
MAX_CONSECUTIVE_LOSSES_PER_STRATEGY
```

Default reversal cap should be `0` or tiny.

Commit:

```text
Add strategy policy and family risk caps
```

---

# Phase 9 — kill switches

## Goal

Make the bot fail closed.

Add:

```text
DISABLE_ALL_NEW_ENTRIES
DISABLE_VALUE_ENTRIES
DISABLE_EVENT_CONTINUATION_ENTRIES
DISABLE_EVENT_REVERSAL_ENTRIES
DISABLE_DSWING_ENTRIES
EXIT_ONLY_MODE
```

Automatic halt triggers:

```text
model_unavailable spike
book_stale ratio spike
mapping errors spike
two catastrophic losses in one strategy
daily drawdown hit
disk guard fails
live reconciliation mismatch
source heartbeat stale
```

Commit:

```text
Add strategy kill switches
```

---

# Phase 10 — post-patch operating plan

Once the safety patches are in, do **not** scale immediately.

## Step 1 — freeze thresholds

Run 3–7 days of clean paper / guarded sim.

Configuration:

```text
VALUE_EDGE enabled
EVENT_CONTINUATION enabled if stable
DSWING enabled if collecting convergence data
EVENT_REVERSAL logging-only or cap=0
ML disabled
legacy entries disabled
```

## Step 2 — build funnel

Track:

```text
TopLive snapshots
valid fair evaluations
model unavailable by reason
strategy candidates
strategy rejects
allocator winners
allocator blocked candidates
execution attempts
fills
exits
```

## Step 3 — analyze by book

### VALUE

```text
fair calibration
settlement ROI
edge bucket ROI
game-time bucket ROI
ask bucket ROI
book-age bucket ROI
```

### EVENT_CONTINUATION

```text
fair_delta bucket
market_reprice bucket
event_reprice_gap bucket
30/60/120s markout
incremental ROI over VALUE
```

### EVENT_REVERSAL

```text
max_bid_120s - entry_ask
bid_120s - entry_ask
timeout loss
spread cost
settlement fallback ROI
```

### DSWING

```text
entry_p_game_used
entry_series_fair
entry_edge
map_end_detected_ns
exit_delay_sec
captured_edge
```

## Step 4 — promotion matrix

Classify every strategy/bucket:

```text
ACTIVE
PAPER_ONLY
LOGGING_ONLY
DISABLED
NEEDS_MORE_DATA
```

## Step 5 — tiny live rollout

Stages:

```text
A: paper only
B: guarded executor sim
C: tiny live in one proven VALUE or DSWING bucket
D: expand one bucket at a time
E: strategy-family scaling
```

Initial live settings:

```text
MAX_TRADE_USD tiny
MAX_TOTAL_LIVE_USD tiny
MAX_OPEN_USD_PER_MATCH tiny
MAX_DAILY_DRAWDOWN_USD tiny
EVENT_REVERSAL cap = 0
```

Scale only after:

```text
live execution slippage matches assumptions
no unclassified losses
bucket ROI remains positive
drawdown inside cap
every trade explainable
```

---

# Immediate next PR scope

Do not implement the whole roadmap in one PR.

The next PR should be:

```text
Remove ML runtime path and finish fair availability safety
```

Contents:

```text
1. Delete handoff_fix.patch.
2. Remove dota_fair_model imports/load/ML_ARBITRAGE from main.py.
3. Fix fair_value.py record_history=False.
4. Add phase_shrink / confidence_multiplier.
5. Add DSWING model_available guard.
6. Fix live_exit_engine current fair availability.
7. Update allocator to log uncontested winners.
8. Add focused tests.
```

That is the correct next move. New strategy logic, sizing, and reports come after this safety PR.
