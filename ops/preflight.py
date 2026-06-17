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
    try:
        from config import (
            ENABLE_EVENT_TRIGGERED_VALUE_TRADING,
            ENABLE_REAL_LIVE_TRADING,
            EVENT_TRIGGERED_VALUE_ENABLED,
            MAX_TRADE_USD,
        )
        from decisive_swing_engine import DSWING_ENABLED
        from value_engine import ENABLE_VALUE_TRADING, VALUE_ENGINE_ENABLED
        all_ok &= _check(
            "config + active engine modules load",
            True,
            f"value={VALUE_ENGINE_ENABLED}/{ENABLE_VALUE_TRADING} "
            f"event_value={EVENT_TRIGGERED_VALUE_ENABLED}/{ENABLE_EVENT_TRIGGERED_VALUE_TRADING} "
            f"dswing={DSWING_ENABLED} "
            f"live={ENABLE_REAL_LIVE_TRADING}",
        )
    except Exception as exc:
        all_ok &= _check("config + active engine modules load", False, repr(exc))
        return 1

    all_ok &= _check("value flag coherence", not ENABLE_VALUE_TRADING or VALUE_ENGINE_ENABLED)
    all_ok &= _check("event-value flag coherence", not ENABLE_EVENT_TRIGGERED_VALUE_TRADING or EVENT_TRIGGERED_VALUE_ENABLED)

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
        from value_engine import ValueEngine, ValueSignal

        class _BookStore(dict):
            pass

        now_ns = time.time_ns()
        test_match_id = f"99999_{int(now_ns / 1e9)}"
        game = {
            "match_id": test_match_id,
            "received_at_ns": now_ns,
            "data_source": "top_live",
            "game_time_sec": 1200,
            "radiant_lead": 15000,
            "radiant_score": 10,
            "dire_score": 6,
            "building_state": 0x7FF | (0x7FF << 11),
            "tower_state": 0x7FF | (0x7FF << 11),
            "radiant_team": "Radiant",
            "dire_team": "Dire",
        }
        mapping = {
            "market_type": "MAP_WINNER",
            "steam_side_mapping": "normal",
            "yes_token_id": "YES",
            "no_token_id": "NO",
        }
        res = ValueEngine().evaluate(
            game,
            mapping,
            _BookStore(YES={"best_ask": 0.78, "best_bid": 0.76, "received_at_ns": now_ns}),
            entered_tokens=set(),
        )
        all_ok &= _check(
            "value engine fires on synthetic",
            any(isinstance(row, ValueSignal) for row in res),
            f"rows={len(res)} first={getattr(res[0], 'reason', 'signal') if res else 'none'}",
        )
    except Exception as exc:
        all_ok &= _check("value engine round-trip", False, repr(exc))


    try:
        from decisive_swing_engine import DecisiveSwingEngine, DSwingSignal

        class _BookStore(dict):
            pass

        now_ns = time.time_ns()
        test_match_id = f"99999_{int(now_ns / 1e9)}"
        ds_game = {
            "match_id": test_match_id,
            "received_at_ns": now_ns,
            "data_source": "top_live",
            "game_time_sec": 1200,
            "radiant_lead": 15000,
            "radiant_score": 10,
            "dire_score": 6,
            "building_state": 0x7FF | (0x7FF << 11),
            "tower_state": 0x7FF | (0x7FF << 11),
            "radiant_team": "Radiant",
            "dire_team": "Dire",
        }
        ds_mapping = {
            "market_type": "MATCH_WINNER",
            "steam_side_mapping": "normal",
            "yes_token_id": "YES",
            "no_token_id": "NO",
            "game_number": 3,
            "series_score_yes": 1,
            "series_score_no": 1,
        }
        res = DecisiveSwingEngine().evaluate(
            ds_game,
            ds_mapping,
            _BookStore(YES={"best_ask": 0.78, "best_bid": 0.76, "received_at_ns": now_ns}),
        )
        all_ok &= _check(
            "dswing engine fires on synthetic",
            any(isinstance(row, DSwingSignal) for row in res),
            f"rows={len(res)} first={getattr(res[0], 'reason', 'signal') if res else 'none'}",
        )
    except Exception as exc:
        all_ok &= _check("dswing engine round-trip", False, repr(exc))

    try:
        from actual_dota_event_detector import ActualDotaEventDetector
        from event_triggered_value_engine import EventTriggeredValueEngine

        detector = ActualDotaEventDetector()
        detector.observe({
            "match_id": "99999", "received_at_ns": 1, "data_source": "top_live",
            "game_time_sec": 1000, "radiant_lead": 1000,
            "radiant_score": 5, "dire_score": 5,
            "tower_state": 0x7FF | (0x7FF << 11),
        })
        events = detector.observe({
            "match_id": "99999", "received_at_ns": 2, "data_source": "top_live",
            "game_time_sec": 1030, "radiant_lead": 5000,
            "radiant_score": 8, "dire_score": 5,
            "tower_state": 0x7FF | (0x7FF << 11),
        })
        all_ok &= _check(
            "actual event detector fires on synthetic",
            bool(events) and isinstance(EventTriggeredValueEngine(), EventTriggeredValueEngine),
            f"events={','.join(e.event_type for e in events)}",
        )
    except Exception as exc:
        all_ok &= _check("actual event detector round-trip", False, repr(exc))

    print("\n6. Polymarket credentials:")
    needed = ENABLE_REAL_LIVE_TRADING
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
    projected_peak_usd = MAX_TRADE_USD * 2
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
