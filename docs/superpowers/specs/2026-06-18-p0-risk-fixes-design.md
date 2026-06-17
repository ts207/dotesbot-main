# P0 Risk Fixes Design

## Overview
This design addresses three P0 vulnerabilities in the core trading logic: mapping orientation validation, manual order safety, and live order partial fill / cancel failure budget leaks.

## 1. Mapping Validator (`mapping_validator.py`)
Currently, `validate_mapping_identity` checks if the teams match in either a `normal` or `reversed_side` orientation, but fails to assert that this matches the explicit `steam_side_mapping` configured in the market entry.

**Fix:**
- Extract the explicitly configured `steam_side_mapping` (defaults to "normal" if missing).
- Ensure that the empirical team mapping matches this direction.
- If it does not, append a `mapping_errors` entry (e.g. `team_orientation_mismatch`) and fail validation.

## 2. Manual Orders (`manual_order_policy.py`)
Manual orders bypass automated policy checks but the `evaluate_manual_policy` function is missing key safety gates.

**Fix:**
- Add `mapping.get("mapping_state") == "quarantined"` check. Reject if quarantined.
- Update the function signature to strictly require `operator`, `source`, and `pre_trade_book` arguments (even if they are just logged or present for audit completeness).

## 3. Live Executor (`live_executor.py`)
In `_poll_and_cancel_delayed`, if an order is cancelled or times out, the code currently calls `release_submitted_budget(order_usd)` blindly. If the order was partially filled, this leaks the filled portion out of the budget constraint. Furthermore, if the cancellation API call fails, the budget is still released, creating another leak if the order eventually fills.

**Fix:**
- In the cancellation path, check for `confirmed_filled_usd` (or calculate it).
- Only call `release_submitted_budget(order_usd - confirmed_filled_usd)`.
- If the `cancel_order_by_id` call throws an exception, log the failure and do NOT release the budget, since the order might still be active on the CLOB.

## Testing Strategy
- The user has requested directly fixing the bugs, but we will ensure the code changes are covered by existing test structures or logically sound.
