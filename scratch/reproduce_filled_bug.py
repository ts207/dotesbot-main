import json
from typing import Any

def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def _status_from_response(resp: dict[str, Any]) -> str:
    status = resp.get("status") or resp.get("orderStatus") or resp.get("state")
    if status:
        return str(status)
    if resp.get("success") is True:
        return "success"
    if resp.get("success") is False:
        return "rejected"
    return "unknown"

def _filled_usd_from_response(resp: dict[str, Any], requested_usd: float) -> float:
    status = _status_from_response(resp).lower()
    # "delayed" and "live" mean the order is in the sequencer/book but not yet filled.
    if status in {"delayed", "live"}:
        return 0.0

    explicit_keys = (
        "filledSizeUsd", "filled_size_usd", "filledAmountUsd", "filled_amount_usd",
        "amountFilled", "filledAmount", "filled", "filled_size",
    )
    for key in explicit_keys:
        value = _to_float(resp.get(key))
        if value is not None and value >= 0:
            return min(value, requested_usd)

    taking = _to_float(resp.get("takingAmount") or resp.get("taking_amount"))
    making = _to_float(resp.get("makingAmount") or resp.get("making_amount"))
    if taking is not None and 0 <= taking <= requested_usd * 1.05:
        return min(taking, requested_usd)
    if making is not None and 0 <= making <= requested_usd * 1.05:
        return min(making, requested_usd)

    if resp.get("success") is True and status in {"matched", "success"}:
        return requested_usd
    if status in {"matched"}:
        return requested_usd
    return 0.0

resp = {"errorMsg": "", "makingAmount": "", "orderID": "0x7ce13a25a58d263319367cf3de3f36846a6cf855bcfebd18e635b9b1376753dd", "status": "delayed", "success": True, "takingAmount": ""}
print(f"Status: {_status_from_response(resp)}")
print(f"Filled: {_filled_usd_from_response(resp, 5.0)}")
