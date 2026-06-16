#!/usr/bin/env python3
"""One-shot cleanup of stale OPEN event positions whose markets have SETTLED.

All current OPEN positions were verified via CLOB get_market to be resolved
losers (winner=False, price≈0) or dust — nothing redeemable. They linger OPEN
because the exit engine can't sell a book-less settled token (→ repeated
'LIVE EXIT FAILED missing_bid' spam) and they occupy open-position slots.

Marks them CLOSED with exit_reason containing 'orphan' so the startup
reconciler's orphan-guard won't re-open them (the chain still shows the
worthless shares). Resets the live_state open_positions counter to the true
count. RUN ONLY WHILE THE BOT IS STOPPED (else its next save clobbers this).
"""
import json, time

PF = "logs/live_positions.json"
SF = "logs/live_state.json"

d = json.load(open(PF))
positions = d["positions"]
closed = []
for p in positions:
    if p.get("state") == "OPEN":
        p["state"] = "CLOSED"
        p["exit_reason"] = "orphan_settled_loss"
        p["pending_entry_order_id"] = None
        p["pending_exit_order_id"] = None
        closed.append((p.get("market_name", "")[:40], p.get("shares"), p.get("token_id")))
d["updated_at_ns"] = time.time_ns()
json.dump(d, open(PF, "w"), indent=2)

# true open count = positions still in an active state
ACTIVE = {"OPEN", "EXITING", "PENDING_ENTRY", "PENDING_EXIT_GTC"}
open_count = sum(1 for p in positions if p.get("state") in ACTIVE)

try:
    s = json.load(open(SF))
    s["open_positions"] = open_count
    s["updated_at_ns"] = time.time_ns()
    json.dump(s, open(SF, "w"), indent=2)
except Exception as e:
    print("warn: could not update live_state.json:", e)

print(f"closed {len(closed)} orphan(s); live_state open_positions -> {open_count}")
for nm, sh, tok in closed:
    print(f"  {nm:40} shares={sh}")
