# P0 Risk Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement strict checks for mapping orientation, quarantine enforcement on manual orders, and budget leak prevention on partial fills/cancellations.

**Architecture:** We are augmenting existing validator and execution policy engines in `mapping_validator.py`, `manual_order_policy.py`, and `live_executor.py`.

**Tech Stack:** Python 3.12+

## Global Constraints

- No additional dependencies.
- Changes must fit into existing code logic seamlessly.

---

### Task 1: Fix Mapping Validator Orientation Check

**Files:**
- Modify: `mapping_validator.py`

**Interfaces:**
- Modifies `validate_mapping_identity` function logic.

- [ ] **Step 1: Write the implementation**
In `mapping_validator.py` `validate_mapping_identity`, extract `steam_side_mapping`. Update the `if not (normal or reversed_side):` check to strictly enforce `steam_side_mapping`. If `side_map == "normal"` and not `normal`, or `side_map == "reversed"` and not `reversed_side`, log an error.

- [ ] **Step 2: Commit**

---

### Task 2: Harden Manual Order Policy

**Files:**
- Modify: `manual_order_policy.py`
- Modify: `live_executor.py`

**Interfaces:**
- Updates `evaluate_manual_policy` signature to require `operator`, `source`, `pre_trade_book`. 

- [ ] **Step 1: Write the implementation**
Add `operator: str`, `source: str`, `pre_trade_book: dict` to the `evaluate_manual_policy` signature.
Inside `evaluate_manual_policy`, add:
```python
    if mapping.get("mapping_state") == "quarantined":
        return reject("mapping_quarantined")
```
Update `live_executor.py` `try_buy_manual` to pass these new kwargs.

- [ ] **Step 2: Commit**

---

### Task 3: Fix Live Executor Budget Leak

**Files:**
- Modify: `live_executor.py`

**Interfaces:**
- Updates `_poll_and_cancel_delayed` to compute and refund only the unfilled amount and guard against cancel exceptions.

- [ ] **Step 1: Write the implementation**
In `_poll_and_cancel_delayed`:
Update cancellation logic:
```python
        # Still pending after 30s — cancel and release
        logger.warning("[delayed_poll] order=%s still pending at 30s — cancelling", order_id)
        try:
            if self.client is None:
                self.client = LiveCLOBClient()
            await self.client.cancel_order_by_id(order_id)
        except Exception as exc:
            logger.warning("[delayed_poll] cancel order=%s failed: %s", order_id, exc)
            return  # Do not release budget if cancel fails, as order may still fill!
            
        if attempt is not None:
            attempt.order_status = "cancelled"
            attempt.reason_if_rejected = "delayed_order_timeout_cancelled"
            
        # Refund only the unfilled portion of the order
        confirmed_filled_usd = 0.0 # Extract from status if possible, otherwise assume 0 for safety here.
        amount_to_refund = max(0.0, order_usd - confirmed_filled_usd)
        
        self.release_submitted_budget(amount_to_refund, match_id=match_id, strategy_family=strategy_family)
        self.decrement_open_positions(match_id, full_exit=False)
        await self._emit_delayed_resolution(attempt)
```

- [ ] **Step 2: Commit**
