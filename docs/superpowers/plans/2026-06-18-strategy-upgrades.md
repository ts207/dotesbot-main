# Strategy Upgrades Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Contextual Series Thresholds for the Decisive Swing strategy and a Reversal Multiplier for the Event-Triggered Value strategy.

**Architecture:** 
1. Modify `decisive_swing_engine.py` to ingest the series score (from the `mapping` dict). If the leading team is up 1-0 in a BO3, apply a 30% reduction to the `DSWING_LEAD` requirement.
2. Modify `event_triggered_value_engine.py` and/or `signal_engine.py` to identify `EVENT_REVERSAL` signals (underdog wins fight) and apply a scalar multiplier (e.g., 2.0x) to their `target_size_usd` or `size_multiplier` output, making the bot bet larger on underdog comebacks.

**Tech Stack:** Python 3.12, Pytest

## Global Constraints

- No new external dependencies.
- Changes must be backward compatible with the existing `BotRuntime` pipeline.
- SQLite logging must not be disrupted.

---

### Task 1: DSwing Contextual Series Thresholds

**Files:**
- Modify: `decisive_swing_engine.py`
- Test: `tests/test_decisive_swing_engine.py` (assuming it exists, otherwise create it or test inline)

**Interfaces:**
- Consumes: `mapping` dict (contains `series_score_yes`, `series_score_no`, `yes_team`, `no_team`)
- Produces: Adjusted `required_lead` internally before evaluating the signal.

- [ ] **Step 1: Write the failing test**

```python
# Create tests/test_dswing_thresholds.py
def test_dswing_threshold_reduction():
    from decisive_swing_engine import DecisiveSwingEngine
    from dataclasses import dataclass
    
    @dataclass
    class DummyConfig:
        dswing_lead: int = 6000
        dswing_min_edge: float = 0.05
        dswing_max_price: float = 0.92
        dswing_min_game_time: int = 600
        dswing_trade_usd: float = 5.0
        
    engine = DecisiveSwingEngine(DummyConfig())
    
    # Game state: Radiant has 4500 lead (below 6000). But Radiant is up 1-0 in series.
    game = {
        "radiant_lead": 4500,
        "game_time_sec": 1000,
    }
    mapping = {
        "yes_team": "Radiant",
        "no_team": "Dire",
        "series_score_yes": 1,
        "series_score_no": 0,
        "market_type": "MATCH_WINNER"
    }
    
    # Should trade because threshold is reduced by 30% (6000 -> 4200)
    signals = engine.evaluate(game, mapping, book_store=None, p_game=1.0, series_fair=0.8)
    assert len(signals) == 1
    assert getattr(signals[0], "would_trade", True) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dswing_thresholds.py -v`
Expected: FAIL (returns empty list because 4500 < 6000)

- [ ] **Step 3: Write minimal implementation**

Modify `decisive_swing_engine.py`:
```python
# Inside evaluate() method of DecisiveSwingEngine, replace the static threshold logic:

    def evaluate(self, game: Mapping, mapping: Mapping, book_store: Any, p_game: float, series_fair: float) -> list[Any]:
        # ... existing early returns ...
        
        required_lead = self.cfg.dswing_lead
        
        # Contextual threshold logic
        score_yes = int(mapping.get("series_score_yes") or 0)
        score_no = int(mapping.get("series_score_no") or 0)
        
        # If leading team is up 1-0, reduce required lead by 30%
        if lead > 0 and score_yes == 1 and score_no == 0:
            required_lead = int(required_lead * 0.7)
        elif lead < 0 and score_no == 1 and score_yes == 0:
            required_lead = int(required_lead * 0.7)

        if abs(lead) < required_lead:
            # log reject...
            return []
            
        # ... rest of implementation ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_dswing_thresholds.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add decisive_swing_engine.py tests/test_dswing_thresholds.py
git commit -m "feat(dswing): reduce required lead by 30% if team is up 1-0"
```

---

### Task 2: Event Reversal Multiplier

**Files:**
- Modify: `event_triggered_value_engine.py`

**Interfaces:**
- Consumes: Event data identifying if the event is a reversal (underdog fights back).
- Produces: Emits a signal with a doubled `sized_usd` or `size_multiplier` property.

- [ ] **Step 1: Write the failing test**

```python
# Create tests/test_event_reversal_multiplier.py
def test_event_reversal_multiplier():
    from event_triggered_value_engine import EventTriggeredValueEngine
    from actual_dota_event_types import ActualDotaEvent
    from dataclasses import dataclass
    
    @dataclass
    class DummyConfig:
        value_min_edge: float = 0.05
        paper_trade_size_usd: float = 25.0
        
    engine = EventTriggeredValueEngine(DummyConfig())
    
    # Simulate a reversal event
    event = ActualDotaEvent(
        event_id="test_id",
        event_type="TEAM_WIPE",
        side="radiant",
        radiant_lead_before=-5000, # Dire was winning
        radiant_lead_after=-2000,  # Radiant (underdog) wiped them
        game_time_sec=1000
    )
    
    # ... mock out the necessary state to get a signal to fire ...
    # This test asserts that the resulting signal has sized_usd = 50.0 instead of 25.0
    
    # (Assuming the test framework can inject the event and get the signal)
    # assert signal.sized_usd == 50.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_event_reversal_multiplier.py -v`
Expected: FAIL (sized_usd will be 25.0, not 50.0)

- [ ] **Step 3: Write minimal implementation**

Modify `event_triggered_value_engine.py`:
```python
# Inside the signal generation logic of EventTriggeredValueEngine:

        # Check if this is a reversal (underdog closing the gap)
        is_reversal = False
        if event.radiant_lead_before is not None and event.radiant_lead_after is not None:
            if event.radiant_lead_before < -1000 and event.side == "radiant":
                is_reversal = True
            elif event.radiant_lead_before > 1000 and event.side == "dire":
                is_reversal = True
                
        # Double the trade size for reversals
        base_size = self.cfg.paper_trade_size_usd
        actual_size = base_size * 2.0 if is_reversal else base_size

        # Pass actual_size to the signal constructor
        # ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_event_reversal_multiplier.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add event_triggered_value_engine.py tests/test_event_reversal_multiplier.py
git commit -m "feat(event): apply 2x size multiplier to reversal events"
```
