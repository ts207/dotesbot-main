# Batch 11: Exit Observation Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement an observation ledger to record exit decisions and outcomes for evaluating exit rules.

**Architecture:** A new module `exit_observation.py` with helpers for building and writing observation rows, integrated into the `PaperTrader` exit loop.

**Tech Stack:** Python, CSV, standard libraries.

## Global Constraints
- `logs/exit_policy_observations.csv` is the target file.
- `exit_policy.py` thresholds must be used.
- Do not modify existing entry logic or strategy allocator.
- Adhere to TDD: write failing tests first.

---

### Task 1: Scaffolding and `build_exit_observation_row` (Basic)

**Files:**
- Create: `exit_observation.py`
- Create: `tests/test_exit_observation.py`

- [ ] **Step 1: Write failing test for basic row building**
```python
def test_observation_row_metadata():
    from exit_observation import build_exit_observation_row
    pos = {
        "token_id": "tok1",
        "match_id": "m1",
        "side": "YES",
        "entry_price": 0.5,
        "shares": 100,
        "cost_usd": 50,
        "entry_time_ns": 1000,
        "strategy_family": "VALUE",
        "strategy_kind": "VALUE_EDGE",
        "hold_policy": "thesis_invalidation"
    }
    row = build_exit_observation_row(
        position=pos,
        book={"best_bid": 0.55, "best_ask": 0.57},
        now_ns=2000
    )
    assert row["token_id"] == "tok1"
    assert row["current_bid"] == 0.55
    assert row["age_sec"] == (2000 - 1000) / 1e9
```

- [ ] **Step 2: Run test and verify it fails**
- [ ] **Step 3: Implement basic `build_exit_observation_row`**
- [ ] **Step 4: Run test and verify it passes**

### Task 2: Implement Trigger Logic in `build_exit_observation_row`

**Files:**
- Modify: `exit_observation.py`
- Modify: `tests/test_exit_observation.py`

- [ ] **Step 1: Write failing tests for triggers**
(Catastrophe, Fair Invalidation, Game Over, Max Hold)
- [ ] **Step 2: Run tests and verify they fail**
- [ ] **Step 3: Implement trigger checks in `build_exit_observation_row`**
Replicate logic from `ExitPolicy.decide` for these specific triggers.
- [ ] **Step 4: Run tests and verify they pass**

### Task 3: Implement `write_exit_observation`

**Files:**
- Modify: `exit_observation.py`
- Modify: `tests/test_exit_observation.py`

- [ ] **Step 1: Write failing test for CSV writing**
Verify header is written once and rows are appended.
- [ ] **Step 2: Run test and verify it fails**
- [ ] **Step 3: Implement `write_exit_observation`**
- [ ] **Step 4: Run test and verify it passes**

### Task 4: Integration into `bot_runtime.py`

**Files:**
- Modify: `runtime/bot_runtime.py`

- [ ] **Step 1: Integrate `build_exit_observation_row` and `write_exit_observation` in the `closed` loop**
- [ ] **Step 2: Manual verification (or integration test if possible)**

### Task 5: Final Verification

- [ ] **Step 1: Run all tests**
`pytest tests/test_exit_observation.py`
`pytest tests/test_outcome_attribution.py`
`pytest tests/`
- [ ] **Step 2: Commit changes**
`git add exit_observation.py tests/test_exit_observation.py runtime/bot_runtime.py`
`git commit -m "Batch 11: add exit policy observation ledger"`
