"""Read-only risk rail audit for live-trading configuration.

This helper does not edit .env. It reports whether the currently supplied rails
are appropriate for a small paper/live test bankroll.
"""
from __future__ import annotations

import argparse
import os
from typing import Any, Mapping


SUGGESTED_MAX = {
    "MAX_TRADE_USD": 5.0,
    "VALUE_MAX_PER_MATCH": 6.0,
    "MAX_TOTAL_LIVE_USD": 15.0,
    "MAX_DAILY_DRAWDOWN_USD": 5.0,
    "MAX_OPEN_POSITIONS": 2,
}


def _float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def audit_risk_config(nav: float, config: Mapping[str, Any]) -> dict:
    findings = []
    normalized = {
        "MAX_TRADE_USD": _float(config.get("MAX_TRADE_USD")),
        "VALUE_MAX_PER_MATCH": _float(config.get("VALUE_MAX_PER_MATCH")),
        "MAX_TOTAL_LIVE_USD": _float(config.get("MAX_TOTAL_LIVE_USD")),
        "MAX_DAILY_DRAWDOWN_USD": _float(config.get("MAX_DAILY_DRAWDOWN_USD")),
        "MAX_OPEN_POSITIONS": _int(config.get("MAX_OPEN_POSITIONS")),
    }

    for key, suggested in SUGGESTED_MAX.items():
        value = normalized[key]
        if value > suggested:
            findings.append({
                "level": "WARN",
                "key": key,
                "value": value,
                "suggested_max": suggested,
                "message": f"{key}={value:g} exceeds suggested max {suggested:g}",
            })

    total_live = normalized["MAX_TOTAL_LIVE_USD"]
    if nav > 0 and total_live > nav * 0.35:
        findings.append({
            "level": "WARN",
            "key": "MAX_TOTAL_LIVE_USD",
            "value": total_live,
            "suggested_max": round(nav * 0.35, 2),
            "message": f"MAX_TOTAL_LIVE_USD={total_live:g} exceeds 35% of NAV ${nav:.2f}",
        })

    trade = normalized["MAX_TRADE_USD"]
    if nav > 0 and trade > nav * 0.12:
        findings.append({
            "level": "WARN",
            "key": "MAX_TRADE_USD",
            "value": trade,
            "suggested_max": round(nav * 0.12, 2),
            "message": f"MAX_TRADE_USD={trade:g} exceeds 12% of NAV ${nav:.2f}",
        })

    return {
        "nav": float(nav),
        "config": normalized,
        "findings": findings,
        "ok": not findings,
    }


def config_from_env(env: Mapping[str, str] | None = None) -> dict:
    env = env or os.environ
    return {key: env.get(key) for key in SUGGESTED_MAX}


def config_from_runtime() -> dict:
    import config

    return {
        "MAX_TRADE_USD": config.MAX_TRADE_USD,
        "VALUE_MAX_PER_MATCH": config.VALUE_MAX_PER_MATCH,
        "MAX_TOTAL_LIVE_USD": config.MAX_TOTAL_LIVE_USD,
        "MAX_DAILY_DRAWDOWN_USD": config.MAX_DAILY_DRAWDOWN_USD,
        "MAX_OPEN_POSITIONS": config.MAX_OPEN_POSITIONS,
    }


def print_audit(audit: Mapping[str, Any]) -> None:
    print(f"nav: {audit['nav']:.2f}")
    for key, value in audit["config"].items():
        print(f"{key}: {value:g}")
    if audit["ok"]:
        print("status: OK")
        return
    print("status: WARN")
    for finding in audit["findings"]:
        print(f"WARN {finding['message']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit live risk rails without editing .env")
    parser.add_argument("--nav", type=float, required=True, help="Current NAV in USD")
    args = parser.parse_args()
    print_audit(audit_risk_config(args.nav, config_from_runtime()))


if __name__ == "__main__":
    main()
