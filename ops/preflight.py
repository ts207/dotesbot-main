#!/usr/bin/env python3
"""Pre-flight check before starting the bot.

Run this before `python3 supervisor.py` for deployment.

Exit 0 if all checks pass; non-zero on any failure.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _check(name: str, ok: bool, detail: str = "") -> bool:
    status = "OK" if ok else "FAIL"
    print(f"  {status:<4} {name:<45} {detail}")
    return ok


def _survival_profile_check() -> bool:
    try:
        from survival_strategy import audit_config, audit_decision, latest_monitor_context, parse_env_file

        findings = audit_config(env=parse_env_file(), monitor=latest_monitor_context())
        decision = audit_decision(findings)
        critical = [f for f in findings if f.level == "critical"]
        warn = [f for f in findings if f.level == "warn"]
        detail = f"decision={decision} critical={len(critical)} warn={len(warn)}"
        if critical:
            detail += f" first={critical[0].code}"
        return _check("survival strategy profile", decision != "NO_GO", detail)
    except Exception as exc:
        return _check("survival strategy profile", False, repr(exc))


def _load_market_rows_for_preflight() -> list[dict[str, str]]:
    path = REPO_ROOT / "markets.yaml"
    try:
        import yaml

        with open(path, encoding="utf-8") as f:
            mk = yaml.safe_load(f)
        return list(mk.get("markets", []))
    except ModuleNotFoundError:
        rows: list[dict[str, str]] = []
        current: dict[str, str] | None = None
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if line.startswith("- "):
                if current is not None:
                    rows.append(current)
                current = {}
                line = line[2:].strip()
            if current is not None and ":" in line:
                key, value = line.split(":", 1)
                current[key.strip()] = value.strip().strip('"').strip("'")
        if current is not None:
            rows.append(current)
        return rows


def main() -> int:
    print(f"\n=== PRE-FLIGHT CHECK at {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())} ===\n")
    all_ok = True

    print("1. Configuration:")
    all_ok &= _survival_profile_check()

    try:
        from continuous_engine import CONTINUOUS_ENGINE_ENABLED, ENABLE_CONTINUOUS_TRADING
        from arb_engine import ARB_ENGINE_ENABLED, ENABLE_ARB_TRADING, ARB_MAX_OPEN_POSITIONS
        from scalp_executor import SCALP_ENABLED
        from config import (
            ENABLE_REAL_LIVE_TRADING,
            LIVE_TRADING,
            MAX_TOTAL_LIVE_USD,
            MAX_TRADE_USD,
        )
        all_ok &= _check(
            "config + engine modules load",
            True,
            f"cont={CONTINUOUS_ENGINE_ENABLED}/{ENABLE_CONTINUOUS_TRADING} "
            f"arb={ARB_ENGINE_ENABLED}/{ENABLE_ARB_TRADING} "
            f"scalp={SCALP_ENABLED} live={ENABLE_REAL_LIVE_TRADING}",
        )
    except Exception as exc:
        all_ok &= _check("config + engine modules load", False, repr(exc))
        return 1

    if ENABLE_CONTINUOUS_TRADING and not CONTINUOUS_ENGINE_ENABLED:
        all_ok &= _check("continuous flag coherence", False, "ENABLE_CONTINUOUS_TRADING=true but engine off")
    else:
        all_ok &= _check("continuous flag coherence", True)

    if ENABLE_ARB_TRADING and not ARB_ENGINE_ENABLED:
        all_ok &= _check("arb flag coherence", False, "ENABLE_ARB_TRADING=true but engine off")
    else:
        all_ok &= _check("arb flag coherence", True)

    print("\n2. Market mappings:")
    try:
        markets = _load_market_rows_for_preflight()
        mapped = [
            m
            for m in markets
            if m.get("dota_match_id")
            and str(m["dota_match_id"]).isdigit()
            and str(m["dota_match_id"]) != "123"
        ]
        all_ok &= _check("markets.yaml readable", True, f"{len(mapped)} real-mapped markets")
        if len(mapped) < 10:
            all_ok &= _check("mapped market count", False, f"only {len(mapped)} mapped; bot will fire rarely")
    except Exception as exc:
        all_ok &= _check("markets.yaml readable", False, repr(exc))

    print("\n3. Historical data:")
    data_v2 = REPO_ROOT / "data_v2"
    for table in ["snapshots", "book_ticks"]:
        p = data_v2 / table
        partitions = list(p.glob("date=*")) if p.exists() else []
        files = sum(1 for _ in p.rglob("*.parquet")) if p.exists() else 0
        all_ok &= _check(f"data_v2/{table}", files > 0, f"{len(partitions)} date partitions, {files} parquet files")

    print("\n4. Logging:")
    logs_dir = REPO_ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    try:
        test_file = logs_dir / ".preflight_test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        all_ok &= _check("logs/ writable", True)
    except Exception as exc:
        all_ok &= _check("logs/ writable", False, repr(exc))

    print("\n5. Strategy smoke test:")
    try:
        from continuous_scorer import ContinuousSignal, score_snapshot

        prev = {
            "match_id": "99999",
            "received_at_ns": 1_000_000_000_000_000_000,
            "game_time_sec": 1800,
            "radiant_lead": 500,
            "radiant_score": 10,
            "dire_score": 10,
        }
        cur = {
            "match_id": "99999",
            "received_at_ns": 1_000_000_020_000_000_000,
            "game_time_sec": 1820,
            "radiant_lead": 2500,
            "radiant_score": 12,
            "dire_score": 10,
        }
        yes_book = {"best_bid": 0.54, "best_ask": 0.56, "mid": 0.55, "ask_size": 100, "bid_size": 150}
        no_book = {"best_bid": 0.44, "best_ask": 0.46, "mid": 0.45, "ask_size": 100, "bid_size": 150}
        res = score_snapshot(
            prev_snap=prev,
            cur_snap=cur,
            yes_book=yes_book,
            no_book=no_book,
            pregame_yes_mid=0.62,
            mapping={"steam_side_mapping": "normal"},
        )
        all_ok &= _check(
            "continuous_scorer fires on synthetic",
            isinstance(res, ContinuousSignal),
            f"side={getattr(res, 'side', '?')} sized=${getattr(res, 'sized_usd', 0):.2f}",
        )
    except Exception as exc:
        all_ok &= _check("continuous_scorer round-trip", False, repr(exc))

    try:
        from arb_scanner import ArbOpportunity, scan_pair

        res = scan_pair(
            yes_book={"best_ask": 0.40, "best_bid": 0.39, "ask_size": 1000, "bid_size": 1000},
            no_book={"best_ask": 0.50, "best_bid": 0.49, "ask_size": 1000, "bid_size": 1000},
            mapping={"market_id": "M1", "dota_match_id": "99999", "yes_token_id": "YT", "no_token_id": "NT"},
            received_at_ns=time.time_ns(),
            total_capital_usd=10.0,
        )
        all_ok &= _check("arb_scanner fires on synthetic", isinstance(res, ArbOpportunity), f"profit={getattr(res, 'profit_cents', 0):.1f}c")
    except Exception as exc:
        all_ok &= _check("arb_scanner round-trip", False, repr(exc))

    print("\n6. Polymarket credentials:")
    needed = ENABLE_REAL_LIVE_TRADING or ENABLE_CONTINUOUS_TRADING or ENABLE_ARB_TRADING
    for key in [
        "POLY_FUNDER_ADDRESS",
        "POLY_PRIVATE_KEY",
        "POLY_CLOB_API_KEY",
        "POLY_CLOB_SECRET",
        "POLY_CLOB_PASS_PHRASE",
    ]:
        val = os.getenv(key, "")
        ok = (len(val) > 10) if needed else True
        detail = "" if ok else "missing or placeholder"
        if not needed:
            detail = "(not checked; live disabled)"
        all_ok &= _check(key, ok, detail)

    print("\n7. Capital headroom check:")
    projected_peak_usd = 0.0
    if ENABLE_CONTINUOUS_TRADING:
        projected_peak_usd += MAX_TRADE_USD * 5
    if ENABLE_ARB_TRADING:
        from arb_scanner import ARB_TOTAL_CAPITAL_USD

        projected_peak_usd += ARB_TOTAL_CAPITAL_USD * ARB_MAX_OPEN_POSITIONS
    if SCALP_ENABLED:
        projected_peak_usd += 100
    if ENABLE_REAL_LIVE_TRADING:
        all_ok &= _check("USDC balance >= peak", True, f"projected peak ${projected_peak_usd:.0f}; verify USDC balance manually")
    else:
        all_ok &= _check("USDC balance check skipped", True, f"(live disabled, projected peak ${projected_peak_usd:.0f})")

    print()
    if all_ok:
        print("ALL PREFLIGHT CHECKS PASSED - safe for the current configured mode.")
        print("This is not live-trading approval; check reports/survival_strategy_report.md first.")
        return 0
    print("PREFLIGHT FAILURES DETECTED - fix before starting bot.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
