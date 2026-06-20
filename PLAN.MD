## Comprehensive plan: upgrade `MODEL_VALUE_EDGE` from scaffold to validated strategy

Objective:

```text
Turn MODEL_VALUE_EDGE into a robust market-aware Dota/Polymarket value strategy:
  market price as prior
  Dota state as residual signal
  strict persistence
  clean paper validation
  no real trading until live evidence passes
```

Current state:

```text
The wrapper is mostly correct.
The predictor is too primitive.
The risk controls need tightening before deployment.
```

The model currently uses only `token_net_worth_lead` and `token_score_margin`, as shown in `features.json`.  The model JSON is only two small trees: one net-worth tree and one score-margin tree.  So this is not yet a serious market-residual model.

---

# Phase 0 — Immediate safety fixes

Do these before any paper/shadow-live run.

## 0.1 Default model trading off

Current issue:

```python
ENABLE_MODEL_VALUE_TRADING defaults to true
```

That is wrong for a new strategy.

Change to:

```python
ENABLE_MODEL_VALUE_TRADING = os.getenv(
    "ENABLE_MODEL_VALUE_TRADING", "false"
).lower() in {"1", "true", "yes"}
```

Rationale:

```text
MODEL_VALUE_EDGE should be explicit opt-in.
No new alpha path should trade by default.
```

Acceptance test:

```text
With no env vars:
  MODEL_VALUE_ENABLED can load
  ENABLE_MODEL_VALUE_TRADING is false
  no ModelValue candidate reaches execution
```

---

## 0.2 Do not let unconfirmed signals preempt confirmed strategies

Current flow:

```text
Model signal passes edge filter
confirmation says "armed but not confirmed"
candidate still enters allocator
execution later returns waiting_confirmation
```

This can block a lower-priority confirmed `VALUE_EDGE` candidate because `MODEL_VALUE_EDGE` has higher allocator priority. The allocator priority is currently `EVENT_CONTINUATION_EDGE`, `MODEL_VALUE_EDGE`, `VALUE_EDGE`, `EVENT_REVERSAL_EDGE`, `DSWING`.

Fix in `strategy_collection.py`:

```python
confirmed = True
if ctx.model_value_confirmation_fn is not None:
    confirmed, confirm_reason = ctx.model_value_confirmation_fn(result)

if not confirmed:
    # log signal + confirmation wait
    # do NOT append StrategyCandidate
    continue
```

Current collection appends the candidate even after storing `would_pass_confirmation`.  Execution then blocks it later.  Move the block earlier.

Acceptance test:

```text
Given:
  unconfirmed MODEL_VALUE_EDGE
  confirmed VALUE_EDGE on same token

Expected:
  VALUE_EDGE can allocate/execute
  MODEL_VALUE_EDGE only logs armed confirmation
```

---

## 0.3 Remove `paper_research` hardcoding from engine policy call

Current issue:

```python
ModelValueEngine.evaluate() calls evaluate_policy(... mode="paper_research")
```

That can mislabel signals as passing research policy even if live-parity would reject them.

Fix:

```text
Preferred:
  remove policy evaluation from ModelValueEngine
  annotate policy only in collection/execution layer with actual runtime mode

Acceptable:
  pass actual mode into StrategyCollectionContext
  then pass mode into ModelValueEngine.evaluate()
```

Recommended design:

```text
ModelValueEngine:
  only computes alpha + rejects bad strategy data

Execution/paper layer:
  annotates would_pass_live / policy_allowed / risk_tags
```

Acceptance test:

```text
In live_parity mode, policy fields reflect live_parity.
In research mode, policy fields reflect research.
No stale paper_research metadata leaks into live-parity logs.
```

---

## 0.4 Store real model features on entry

Current issue:

```python
entry_radiant_lead=0.0
```

is stored for `MODEL_VALUE_EDGE` live positions.

Fix: store actual features.

Add to `ModelValueSignal`:

```python
token_net_worth_lead: float
token_score_margin: float
radiant_net_worth: float
dire_net_worth: float
radiant_score: float
dire_score: float
```

Then store:

```python
entry_radiant_lead = radiant_net_worth - dire_net_worth
entry_token_net_worth_lead = sig.token_net_worth_lead
entry_token_score_margin = sig.token_score_margin
entry_model_version = sig.model_version
```

Acceptance test:

```text
After a paper/live model-value entry:
  closed/open position row has nonzero real lead data
  model version is persisted
  side-oriented feature values match signal side
```

---

## 0.5 Clean `markets.yaml`

Source-controlled `markets.yaml` should not accumulate live runtime mappings, match IDs, current game numbers, or BO3 scores. Runtime auto-bindings belong in runtime overlay storage.

Fix:

```text
Revert runtime-added mappings from markets.yaml.
Keep stable curated markets only.
Put auto_series_binder output in logs/runtime_markets.yaml or equivalent overlay.
```

Acceptance test:

```text
markets.yaml contains no stale live dota_match_id churn.
runtime overlay contains mutable auto-bound markets.
mapping loader still merges base + overlay correctly.
```

---

# Phase 1 — Keep current `MODEL_VALUE_EDGE` as v0, but label it honestly

Rename the current predictor conceptually:

```text
MODEL_VALUE_EDGE v0 = state-only tree heuristic
```

Do not call it a robust LightGBM model.

Current predictor behavior:

```text
features: net-worth lead + kill margin
trees: 2
output: coarse probability buckets
edge: p_state_only - ask
```

The predictor loads JSON, checks feature availability, evaluates each tree recursively, sums leaf values, then applies sigmoid.

## Required metadata change

Update `metadata.json` with caveats:

```json
{
  "model_name": "model_value_edge_v0_state_tree",
  "model_class": "state_only_threshold_tree",
  "uses_market_price": false,
  "uses_game_time": false,
  "uses_objectives": false,
  "uses_orderbook_dynamics": false,
  "deployment_status": "paper_only",
  "known_limitations": [
    "state-only model",
    "ignores market mid",
    "ignores game time",
    "ignores towers, barracks, roshan, lineups",
    "coarse probability buckets",
    "small backtest sample"
  ]
}
```

Current metadata has backtest fields but lacks limitations and split details.

Acceptance test:

```text
metadata explicitly says paper_only.
metadata says uses_market_price=false.
paper validation report includes v0 caveats.
```

---

# Phase 2 — Build real `MODEL_VALUE_EDGE v1`

This is the main model upgrade.

## 2.1 Correct thesis

The model should not be:

```text
Dota state predicts winner from scratch.
```

The model should be:

```text
Market price is the prior.
Dota state detects residual mispricing.
```

Target:

```text
model_probability = calibrated true win probability conditional on:
  market price
  current Dota state
  market microstructure
```

Trade condition remains:

```text
model_probability - ask >= 0.15
```

---

## 2.2 Minimum v1 feature set

Add these features:

```text
Market:
  market_mid
  ask
  bid
  spread
  paired_mid_sum
  book_age_ms
  ask_size
  bid_size
  book_imbalance

Dota state:
  token_net_worth_lead
  token_score_margin
  game_time_sec
  token_net_worth_lead_per_min
  token_score_margin_per_min
  abs_net_worth_lead
  lead_bucket
  game_phase

Interactions:
  token_net_worth_lead × log(game_time_sec)
  token_score_margin × log(game_time_sec)
  market_mid × token_net_worth_lead
  market_mid × game_phase

Dynamics:
  net_worth_change_30s
  net_worth_change_60s
  score_change_60s
  market_mid_change_30s
  market_mid_change_60s
  underreaction_60s = dota_state_change - market_price_change
```

Optional but valuable:

```text
Objectives:
  tower differential
  barracks differential
  building-state flags
  roshan/aegis if available
  game_over proximity flags

Context:
  map number
  series score
  team prior / Elo if reliable
```

---

## 2.3 Model variants to train

Train these in chronological walk-forward only:

```text
A. Logistic market-aware win model
B. LightGBM market-aware win model
C. LightGBM residual model
D. Two-stage trade-quality filter
E. Ensemble: calibrated market-aware win + residual model
```

Target definitions:

### Win-probability target

```text
y = official token win
features include market_mid + Dota state
output = p_true
trade edge = p_true - ask
```

### Residual target

```text
residual = official_win - market_mid
output = predicted_residual
p_true = market_mid + predicted_residual
trade edge = p_true - ask
```

### CLV target

```text
target = future_mid_900s - ask
or
target = future_mid_1200s - ask
```

This can be a second-stage filter, not the primary settlement model.

---

## 2.4 Validation design

No row-random CV.

Use:

```text
chronological walk-forward by match
group by match
no same match in train and test
no future rows in training
```

Report:

```text
Probability metrics:
  Brier
  log loss
  AUC
  calibration by decile

Trading metrics:
  bets
  win rate
  avg ask
  profit
  ROI
  900s CLV
  1200s CLV
  latest-fold ROI
  bootstrap ROI CI
  max drawdown
  profit concentration

Stability metrics:
  ROI by fold
  ROI by month/week
  ROI by game-time bucket
  ROI by ask bucket
  ROI by edge bucket
  CLV by fold
```

Minimum acceptance:

```text
>= 50 OOF backtest trades
positive ROI
positive 900s or 1200s CLV
latest fold non-negative or explainable
bootstrap lower bound not deeply negative
no single match explains majority of profit
```

Promotion to paper:

```text
model v1 beats v0 on CLV stability or fold stability,
not just headline ROI.
```

---

# Phase 3 — Improve live/paper logging

## 3.1 Add model-specific signal columns

Every `MODEL_VALUE_EDGE` signal log should include:

```text
model_name
model_version
model_probability
model_raw_score
market_mid
ask
bid
spread
edge_to_ask
book_age_ms
game_time_sec
token_net_worth_lead
token_score_margin
token_net_worth_lead_per_min
market_mid_change_60s
net_worth_change_60s
confirmation_state
confirmation_age_sec
confirmation_ask_delta
policy_allowed
policy_reason
risk_tags
```

## 3.2 Add CLV markout capture

For every paper/live entry:

```text
future_mid_30s
future_mid_120s
future_mid_300s
future_mid_900s
future_mid_1200s
CLV_30s = future_mid_30s - entry_price
CLV_120s
CLV_900s
CLV_1200s
```

## 3.3 Add promotion report generation

Create script:

```text
scripts/build_model_value_validation_report.py
```

Inputs:

```text
strategy_signals.csv
paper positions / live positions
markout logs
settlement shadow output
```

Outputs:

```text
reports/model_value_validation_YYYYMMDD.md
reports/model_value_validation_trades_YYYYMMDD.csv
```

The existing paper validation template is a good start; it asks for paper trades, settlement ROI, 900s/1200s CLV, last-25-trade return, policy bypasses, and mapping errors.

---

# Phase 4 — Engine hardening

## 4.1 Add true first-trade-per-match enforcement

Backtest rule:

```text
first qualifying trade per match only
```

Engine/policy should enforce both token and opposing-token match exposure.

For a mapping:

```python
opposing_token = no_token if token == yes_token else yes_token
```

Reject if:

```text
token already active
opposing token already active
match already has MODEL_VALUE_EDGE position
match already has any VALUE-family position, unless explicitly allowed
```

Acceptance test:

```text
If YES token has open position, NO token model signal is blocked.
If any token in match has model-value position, second model-value signal blocked.
```

---

## 4.2 Separate model-value from old value caps

Current policy treats `MODEL_VALUE_EDGE` as strategy family `VALUE`. That is acceptable initially, but for analysis and risk clarity add:

```text
MODEL_VALUE_MAX_PER_MATCH
MODEL_VALUE_MAX_OPEN_POSITIONS
MODEL_VALUE_TRADE_USD
```

Keep global family cap too.

Recommended defaults:

```env
MODEL_VALUE_TRADE_USD=5.0
MODEL_VALUE_MAX_PER_MATCH=5.0
MODEL_VALUE_MAX_OPEN_POSITIONS=1
MODEL_VALUE_ENABLED=false
ENABLE_MODEL_VALUE_TRADING=false
```

---

## 4.3 Add model artifact validation

On startup, validate:

```text
model file exists
features file exists
metadata file exists
metadata.strategy == MODEL_VALUE_EDGE
metadata.deployment_status in allowed values
features required by model are present
model version logged
```

Fail closed if invalid.

Acceptance test:

```text
missing model.json -> no signals
missing features.json -> no signals
metadata deployment_status=disabled -> no signals
```

---

# Phase 5 — Testing plan

## Unit tests

Add or expand:

```text
tests/test_model_value_predictor.py
tests/test_model_value_engine.py
tests/test_model_value_confirmation.py
tests/test_model_value_allocator.py
tests/test_model_value_policy.py
tests/test_model_value_execution.py
tests/test_model_value_logging.py
```

Required tests:

```text
Predictor:
  loads model
  rejects missing model
  rejects missing required features
  produces probability in [0,1]
  side-oriented features are correct

Engine:
  rejects non-top_live
  rejects game_over
  rejects unsupported market type
  rejects non-game3 MATCH_WINNER
  rejects stale book
  rejects wide spread
  rejects ask out of bounds
  rejects small edge
  evaluates both sides
  chooses highest edge

Confirmation:
  first signal arms only
  second signal confirms
  expired signal resets
  ask worsen resets
  edge drop resets
  unconfirmed signal does not enter allocator

Allocator:
  confirmed MODEL_VALUE_EDGE beats VALUE_EDGE
  unconfirmed MODEL_VALUE_EDGE does not block VALUE_EDGE
  EVENT_CONTINUATION_EDGE still has top priority

Execution:
  paper path enters at ask
  live path routes through try_buy_value
  position stores real model features
  opposing-token exposure blocked

Policy:
  actual mode is used
  no paper_research hardcoding
  real live remains disabled by default
```

---

# Phase 6 — Paper rollout

## 6.1 Research paper mode

Config:

```env
MODEL_VALUE_ENABLED=true
ENABLE_MODEL_VALUE_TRADING=false
VALUE_ENGINE_ENABLED=false
EVENT_TRIGGERED_VALUE_ENABLED=false
DSWING_ENABLED=false
LIVE_TRADING=false
ENABLE_REAL_LIVE_TRADING=false
PAPER_MODE=research
```

Purpose:

```text
Log model signals without entries.
Check signal frequency, reject reasons, mapping quality.
```

Run until:

```text
at least 100 candidate signals
zero mapping failures
feature availability > 95%
```

---

## 6.2 Live-parity paper mode

Config:

```env
MODEL_VALUE_ENABLED=true
ENABLE_MODEL_VALUE_TRADING=true
VALUE_ENGINE_ENABLED=false
EVENT_TRIGGERED_VALUE_ENABLED=false
DSWING_ENABLED=false
LIVE_TRADING=false
ENABLE_REAL_LIVE_TRADING=false
PAPER_MODE=live_parity
```

Purpose:

```text
Enter only trades that would pass live gates.
```

Minimum sample:

```text
100 paper trades
```

Pass criteria:

```text
settlement ROI > 0
900s or 1200s CLV > 0
latest 25 trades not materially negative
no mapping errors
no policy bypasses
no single-match profit dependence
```

---

## 6.3 Shadow-live mode

Config:

```env
MODEL_VALUE_ENABLED=true
ENABLE_MODEL_VALUE_TRADING=true
VALUE_ENGINE_ENABLED=false
EVENT_TRIGGERED_VALUE_ENABLED=false
DSWING_ENABLED=false
LIVE_TRADING=true
ENABLE_REAL_LIVE_TRADING=false
PAPER_MODE=shadow_live
```

Purpose:

```text
Test dry-live executor, balance checks, sizing, DB persistence, and operational behavior.
```

Pass criteria:

```text
no rejected orders due implementation bugs
all dry-live attempts logged
positions close correctly
PnL and CLV reports build cleanly
```

---

# Phase 7 — Real-live gate

Do not enable real-live until all are true:

```text
>= 100 live-parity/shadow-live paper trades
settlement ROI positive
900s or 1200s CLV positive
latest 25 trades not materially negative
zero policy bypasses
zero mapping errors
profit not concentrated in one match
manual review signed off
```

Initial real-live config:

```env
MODEL_VALUE_ENABLED=true
ENABLE_MODEL_VALUE_TRADING=true
ENABLE_MODEL_VALUE_REAL_TRADING=true

LIVE_TRADING=true
ENABLE_REAL_LIVE_TRADING=true

VALUE_ENGINE_ENABLED=false
EVENT_TRIGGERED_VALUE_ENABLED=false
DSWING_ENABLED=false

MODEL_VALUE_TRADE_USD=1.0
MODEL_VALUE_MAX_PER_MATCH=1.0
MODEL_VALUE_MAX_OPEN_POSITIONS=1

MAX_TOTAL_LIVE_USD=5
MAX_TRADE_USD=1
MAX_OPEN_POSITIONS=1
MAX_DAILY_DRAWDOWN_USD=5
```

Real-live should begin at nuisance size only.

---

# Phase 8 — Model v2 roadmap

After v1 is stable, add:

```text
richer Dota features
recent momentum features
objective-state features
market-movement features
team priors
separate early/mid/late calibration
CLV second-stage filter
```

Model v2 should produce:

```text
p_true
predicted_CLV_900s
trade_quality_score
```

Final entry:

```text
p_true - ask >= 0.15
confirmation >= 2
trade_quality_score >= threshold
spread <= 0.05
book_age <= 10s
one trade per match
```

---

# Priority order

Do not do everything at once. Execute in this order:

```text
P0. Flip ENABLE_MODEL_VALUE_TRADING default to false.
P0. Stop unconfirmed model-value signals from reaching allocator.
P0. Remove paper_research hardcoding from engine policy annotation.
P0. Store real model features in positions.
P0. Clean markets.yaml runtime pollution.

P1. Add model artifact validation.
P1. Improve signal/entry logging.
P1. Build validation report script.

P2. Train market-aware v1 model.
P2. Backtest v1 vs v0.
P2. Replace model artifact only if v1 beats v0 on CLV/fold stability.

P3. Paper-test isolated MODEL_VALUE_EDGE.
P3. Shadow-live isolated MODEL_VALUE_EDGE.

P4. Consider real-live nuisance size only after validation gate.
```

## Final target architecture

```text
MODEL_VALUE_EDGE:
  primary learned value strategy

VALUE_EDGE:
  baseline comparison only

EVENT_CONTINUATION_EDGE:
  optional confirmation / secondary signal

EVENT_REVERSAL_EDGE:
  research only

DSWING:
  separate series-market strategy, disabled during model-value validation

Legacy events:
  off
```

## Final thesis to preserve

```text
Market price is the prior.
Live Dota state identifies residual mispricing.
Only trade large persistent underpricing.
Hold to settlement.
Validate with live-paper ROI and 900s/1200s CLV before real capital.
```
