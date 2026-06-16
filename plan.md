Correct. **Event and value strategies are different.** The plan should not collapse them into one “fair edge” framework. They may share `fair_value.py`, but their **alpha thesis, trigger, horizon, exit, and success metrics are different**.

The corrected architecture should treat them as separate strategies that happen to use some shared primitives.

# Correct distinction

## `VALUE_EDGE`

VALUE is a **state mispricing strategy**.

It asks:

```text
Given the current TopLive state, is this side underpriced?
```

It does **not** need a discrete event.

Signal:

```text
current fair_used - ask
```

Target:

```text
settlement / game result
```

Primary horizon:

```text
hold to game_over / settlement
```

Best evidence:

```text
fair bucket calibration
edge bucket settlement ROI
ask bucket ROI
game-time/lead bucket ROI
```

VALUE should be slow, broad, and stable. It is a continuous state strategy.

---

## `EVENT_CONTINUATION_EDGE`

Event continuation is an **event-repricing strategy**.

It asks:

```text
Did a new event create a fair-value jump that the book has not repriced yet?
```

It should not simply mean “VALUE fired after an event.”

Signal:

```text
fair_before
fair_after
fair_delta
market_move_after_event
remaining_repricing_edge
```

A better event continuation edge is:

```text
event_edge = fair_delta_used - market_reprice_since_event
```

or:

```text
remaining_event_edge = fair_after_used - current_ask
```

but the key difference is that the trade is justified by the **change** caused by the event, not only the absolute fair.

Target:

```text
short-horizon repricing and/or settlement
```

Primary horizons:

```text
30s / 60s / 120s markout
plus settlement if proven
```

Best evidence:

```text
post-event markout
event type buckets
fair_delta buckets
market_reprice buckets
incremental ROI over non-event VALUE
```

Event continuation should be judged as a repricing-capture strategy.

---

## `EVENT_REVERSAL_EDGE`

Event reversal is not value. It is not continuation.

It is a **bounce / overreaction strategy**.

It asks:

```text
Did the market overreact to an event, creating a temporary bounce opportunity?
```

Signal should be closer to:

```text
overreaction_score
price_dislocation
bounce_probability
spread-adjusted bounce target
```

not just:

```text
settlement fair - ask
```

Target:

```text
short-horizon bounce
```

Primary horizons:

```text
30s / 60s / 120s
```

Best evidence:

```text
max_bid_within_120s - entry_ask
realizable bounce after spread
timeout loss
settlement fallback ROI
```

Default posture:

```text
disabled / logging-only / tiny cap
```

Reversal should not inherit VALUE’s hold-to-settlement assumptions.

---

# The corrected strategy taxonomy

Use this separation:

```text
VALUE_EDGE:
  source: continuous TopLive state
  trigger: fair_used - ask
  target_horizon: settlement
  exit: game_over / max_hold / fair invalidation
  metric: settlement ROI

EVENT_CONTINUATION_EDGE:
  source: discrete live-grade event
  trigger: fair_delta and unrepriced book
  target_horizon: repricing_30s_60s_120s, optionally settlement
  exit: markout/timeout or thesis invalidation
  metric: post-event markout + incremental settlement ROI

EVENT_REVERSAL_EDGE:
  source: market overreaction after event
  trigger: overreaction/bounce setup
  target_horizon: bounce_30s_60s_120s
  exit: bounce TP / timeout / hard stop
  metric: realizable bounce ROI

DSWING:
  source: map nearly decided inside BO3
  trigger: series_fair - ask
  target_horizon: map_end_convergence
  exit: map-end repricing
  metric: exit_bid - entry_price
```

That is the right decomposition.

# How this changes the patch plan

The earlier plan should be modified in three places.

## 1. Do not make event continuation just another VALUE candidate

`event_triggered_value_engine.py` currently computes `fair_before`, `fair_after`, `fair_delta`, and `edge = fair_after - ask`. That is useful, but the final event signal should explicitly log and gate on **event repricing**, not just absolute value.

Add fields:

```python
market_price_before_event: float | None
market_price_after_event: float | None
market_reprice: float | None

fair_before_raw: float | None
fair_before_used: float | None
fair_after_raw: float | None
fair_after_used: float | None
fair_delta_raw: float | None
fair_delta_used: float | None

remaining_event_edge: float | None
event_reprice_gap: float | None

target_horizon: str
```

For continuation:

```text
target_horizon = "repricing_120s"
```

or, if explicitly intended:

```text
target_horizon = "settlement"
```

But do not leave it implicit.

---

## 2. Add separate event markout logging

VALUE can be judged by settlement.

EVENT needs markouts.

Add or extend:

```text
logs/signal_markouts.csv
```

Required event markouts:

```text
price_at_signal
bid_30s
ask_30s
mid_30s
bid_60s
ask_60s
mid_60s
bid_120s
ask_120s
mid_120s
max_bid_120s
min_bid_120s
realizable_exit_30s
realizable_exit_60s
realizable_exit_120s
```

For EVENT_CONTINUATION:

```text
markout_30s = bid_30s - entry_ask
markout_60s = bid_60s - entry_ask
markout_120s = bid_120s - entry_ask
```

For EVENT_REVERSAL:

```text
bounce_capture = max_bid_120s - entry_ask
timeout_loss = bid_120s - entry_ask
```

Settlement ROI is secondary for reversal.

---

## 3. Allocator priority should depend on target horizon

The allocator currently ranks:

```text
EVENT_CONTINUATION_EDGE
VALUE_EDGE
EVENT_REVERSAL_EDGE
DSWING
```

That is too simple because VALUE and EVENT may target different things.

Correct allocator comparison should consider:

```text
token_id
strategy_kind
target_horizon
capital lockup
exit plan
edge type
```

A continuation signal with `target_horizon=repricing_120s` should not be compared to VALUE purely by edge. It is a short-horizon repricing trade. VALUE is a settlement trade.

Better allocator model:

```text
If same token and same direction:
  repricing event may take priority if it has short-horizon edge and explicit exit

If same token but VALUE already open:
  event continuation may update thesis, not open duplicate

If same token and opposite direction:
  require explicit hedge/reversal policy

If target horizons differ:
  compare expected return per unit time and risk, not just edge
```

Initial implementation can remain simple, but the audit must log `target_horizon` so you can later decide.

# Corrected final plan

The profitable bot should have **three independent alpha books**, not one blended book.

## Book 1 — VALUE book

Purpose:

```text
state mispricing → settlement
```

Trades:

```text
VALUE_EDGE only
```

Reports:

```text
fair calibration
settlement ROI
edge buckets
lead/time buckets
```

Promotion:

```text
scale only calibrated settlement buckets
```

---

## Book 2 — EVENT book

Purpose:

```text
event repricing / overreaction
```

Trades:

```text
EVENT_CONTINUATION_EDGE
EVENT_REVERSAL_EDGE
```

Reports:

```text
event type
fair_delta
market_reprice
remaining_event_edge
30/60/120s markout
bounce capture
incremental ROI over VALUE
```

Promotion:

```text
continuation only if markout or settlement increment is proven
reversal only if bounce is realizable after spread
```

---

## Book 3 — DSWING book

Purpose:

```text
BO3 match-winner convergence at map end
```

Trades:

```text
DSWING
```

Reports:

```text
entry_series_fair
entry_edge
map_end_detected_ns
exit_delay
captured_edge
```

Promotion:

```text
scale only buckets with positive captured_edge
```

# Final corrected roadmap

## Phase A — shared safety layer

Do these for all strategies:

```text
remove ML runtime dependency
harden fair_value.py
add model_available/model_reason
add fair_raw/fair_used
make missing input unavailable
make record_history=False read-only
add strategy-family caps
add kill switches
```

## Phase B — VALUE book

Implement:

```text
VALUE uses fair_used - ask
target_horizon = settlement
log fair_raw/fair_used
log settlement outcome
report calibration and ROI by bucket
```

Do not require event fields.

## Phase C — EVENT book

Implement separately:

```text
event snapshot before/after
fair_before_used
fair_after_used
fair_delta_used
market_reprice_after_event
remaining_event_edge
target_horizon = repricing_30s/60s/120s or settlement
markout logging
event-specific reports
```

For reversal, add:

```text
bounce_target
timeout_sec
max_bid_within_horizon
realizable_bounce_roi
```

Do not use VALUE exits blindly.

## Phase D — DSWING book

Implement separately:

```text
p_game_used
series_fair
map_end_convergence target
exit quality report
captured_edge by bucket
```

## Phase E — allocator

Keep initial fixed priority, but audit correctly:

```text
strategy_kind
target_horizon
edge_type
expected_hold_sec
allocator_winner
blocked_reason
counterfactual markout/settlement
```

Later, priority should be learned by bucket:

```text
VALUE settlement bucket
EVENT markout bucket
DSWING convergence bucket
```

# The key correction

You are right because this statement is wrong:

```text
EVENT_CONTINUATION is just VALUE with an event attached.
```

Correct statement:

```text
VALUE trades absolute state mispricing.
EVENT trades repricing dynamics caused by a discrete event.
DSWING trades series-market convergence after map resolution.
REVERSAL trades short-term overreaction/bounce.
```

They can share fair-value infrastructure, but they must not share the same thesis, horizon, exit, or success metric.

# Final operating rule

For every strategy candidate, require this contract:

```text
strategy_kind:
edge_type:
target_horizon:
entry_trigger:
exit_trigger:
primary_metric:
secondary_metric:
promotion_rule:
disable_rule:
```

Examples:

```text
VALUE_EDGE:
  edge_type: absolute_state_value
  target_horizon: settlement
  entry_trigger: fair_used - ask
  exit_trigger: game_over / fair_invalidation / max_hold
  primary_metric: settlement ROI
  promotion_rule: calibrated positive ROI by bucket

EVENT_CONTINUATION_EDGE:
  edge_type: event_repricing
  target_horizon: repricing_120s
  entry_trigger: fair_delta_used - market_reprice
  exit_trigger: markout target / timeout / thesis invalidation
  primary_metric: 120s realizable markout
  promotion_rule: positive markout or incremental settlement ROI over VALUE

EVENT_REVERSAL_EDGE:
  edge_type: event_overreaction_bounce
  target_horizon: bounce_120s
  entry_trigger: overreaction score + bounce setup
  exit_trigger: bounce TP / timeout
  primary_metric: max realizable bounce after spread
  promotion_rule: positive bounce ROI by event bucket

DSWING:
  edge_type: map_end_series_convergence
  target_horizon: map_end
  entry_trigger: series_fair - ask
  exit_trigger: map_end_convergence
  primary_metric: exit_bid - entry_price
  promotion_rule: positive captured_edge by bucket
```

That is the corrected plan.
