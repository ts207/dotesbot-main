#!/usr/bin/env python3
"""Construct strict as-of market probability proxies for Model B."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import yaml


HEADERS = [
    "market_id",
    "condition_id",
    "event_id",
    "yes_token_id",
    "no_token_id",
    "slug",
    "question",
    "source_universe",
    "game_id",
    "decision_ts",
    "decision_ts_source",
    "decision_config_version",
    "proxy_ts",
    "p_market_early_mid",
    "proxy_source",
    "timestamp_confidence",
    "asof_valid",
    "bidask_available",
    "depth_available",
    "execution_price_available",
    "proxy_notes",
]


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def fnum(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_config(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def first_clean_snapshot_mid(path: Path, max_after_start_sec: int) -> tuple[str, float, str] | None:
    if not path.exists():
        return None
    for row in read_csv(path):
        gt = fnum(row.get("gt_sec"))
        if gt is None or gt > max_after_start_sec:
            continue
        bid = fnum(row.get("yes_bid"))
        ask = fnum(row.get("yes_ask"))
        if bid is None or ask is None or ask < bid:
            continue
        ts = row.get("timestamp_utc") or ""
        return ts, (bid + ask) / 2.0, "book_mid"
    return None


def first_price_history_mid(path: Path) -> tuple[str, float, str, str] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    status = payload.get("fetch_status") or "error"
    if status != "ok":
        return None
    raw = payload.get("raw_response") or {}
    history = raw.get("history") if isinstance(raw, dict) else raw
    if not isinstance(history, list):
        return None
    points = []
    for point in history:
        if not isinstance(point, dict):
            continue
        ts = point.get("t") or point.get("timestamp") or point.get("time")
        price = fnum(point.get("p") or point.get("price"))
        if ts is None or price is None:
            continue
        try:
            ts_dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            parsed = parse_ts(str(ts))
            if parsed is None:
                continue
            ts_dt = parsed
        points.append((ts_dt, price))
    if not points:
        return None
    points.sort(key=lambda x: x[0])
    ts_dt, price = points[0]
    return ts_dt.isoformat(), price, "price_history_proxy", status


def price_history_status(path: Path) -> str:
    if not path.exists():
        return "missing_price_history"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "price_history_parse_error"
    return str(payload.get("fetch_status") or "price_history_unknown_status")


def valid_probability(p: float, cfg: dict[str, Any]) -> bool:
    return float(cfg.get("p_market_min", 0.02)) <= p <= float(cfg.get("p_market_max", 0.98))


def build_row(
    market: dict[str, str],
    clean_by_market: dict[str, dict[str, str]],
    cfg: dict[str, Any],
    price_history_dir: Path,
    clean_v2_snapshots: Path,
) -> dict[str, str]:
    market_id = market.get("market_id", "")
    clean = clean_by_market.get(market_id, {})
    max_after = int(cfg.get("max_after_game_start_seconds", 60))
    source = None
    note = ""
    snap_path = clean_v2_snapshots / f"{market_id}.csv"
    result = first_clean_snapshot_mid(snap_path, max_after)
    if result is None and clean.get("earliest_book_mid") and clean.get("earliest_book_ts"):
        result = (clean["earliest_book_ts"], float(clean["earliest_book_mid"]), "early_snapshot_mid")
    if result is None:
        token_id = market.get("yes_token_id") or clean.get("token_id_yes") or clean.get("yes_token_id") or ""
        history_path = price_history_dir / f"{token_id}.json"
        ph_result = first_price_history_mid(history_path)
        if ph_result is not None:
            proxy_ts, p, source, _status = ph_result
            result = (proxy_ts, p, source)
        else:
            note = price_history_status(history_path)
    if result is None:
        proxy_ts = ""
        p_mid = ""
        source = ""
        decision_ts = ""
        confidence = "0.00"
        asof = "False"
        note = note or "missing_market_probability_proxy"
    else:
        proxy_ts, p, source = result
        decision_ts = proxy_ts
        confidence_value = {"book_mid": 1.0, "early_snapshot_mid": 0.9, "price_history_proxy": 0.7}.get(source, 0.0)
        confidence = f"{confidence_value:.2f}"
        p_mid = f"{p:.6f}"
        proxy_dt = parse_ts(proxy_ts)
        decision_dt = parse_ts(decision_ts)
        asof_bool = proxy_dt is not None and decision_dt is not None and proxy_dt <= decision_dt
        if source in set(cfg.get("disallowed_proxy_sources", [])):
            asof_bool = False
            note = "disallowed_proxy_source"
        if not valid_probability(p, cfg):
            note = "invalid_probability_range"
        asof = str(asof_bool)
    bidask = str(source in {"book_mid", "early_snapshot_mid"})
    return {
        "market_id": market_id,
        "condition_id": market.get("condition_id", "") or clean.get("condition_id", ""),
        "event_id": market.get("event_id", "") or clean.get("event_id", ""),
        "yes_token_id": market.get("yes_token_id", "") or clean.get("token_id_yes", ""),
        "no_token_id": market.get("no_token_id", "") or clean.get("token_id_no", ""),
        "slug": market.get("slug", "") or clean.get("slug", ""),
        "question": market.get("question", "") or clean.get("question", ""),
        "source_universe": market.get("source_universe", "local_clean_v2"),
        "game_id": market.get("game_id", market_id),
        "decision_ts": decision_ts,
        "decision_ts_source": cfg.get("decision_ts_rule", "first_valid_early_market_observation"),
        "decision_config_version": cfg.get("decision_config_version", "decision_ts_v1"),
        "proxy_ts": proxy_ts,
        "p_market_early_mid": p_mid,
        "proxy_source": source or "",
        "timestamp_confidence": confidence,
        "asof_valid": asof,
        "bidask_available": bidask,
        "depth_available": bidask,
        "execution_price_available": bidask,
        "proxy_notes": note,
    }


def brier(rows: list[dict[str, str]], outcomes: dict[str, str]) -> float | None:
    vals = []
    for row in rows:
        p = fnum(row.get("p_market_early_mid"))
        y = fnum(outcomes.get(row.get("market_id", "")))
        if p is not None and y is not None:
            vals.append((p - y) ** 2)
    return mean(vals) if vals else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean-v2", default="data/clean_v2/matches.csv")
    parser.add_argument("--market-map", default="data/processed/market_game_map.csv")
    parser.add_argument("--price-history-dir", default="data/raw/polymarket/price_history")
    parser.add_argument("--clean-v2-snapshots", default="data/clean_v2/snapshots")
    parser.add_argument("--decision-config", default="configs/decision_ts_v1.yaml")
    parser.add_argument("--output", default="data/processed/market_probability_proxies.csv")
    parser.add_argument("--audit-output", default="reports/market_probability_proxy_audit.json")
    args = parser.parse_args()

    cfg = load_config(Path(args.decision_config))
    clean_rows = read_csv(Path(args.clean_v2))
    clean_by_market = {m.get("market_id", ""): m for m in clean_rows if m.get("market_id")}
    markets = [m for m in read_csv(Path(args.market_map)) if m.get("market_id")]
    rows = [
        build_row(m, clean_by_market, cfg, Path(args.price_history_dir), Path(args.clean_v2_snapshots))
        for m in markets
    ]
    write_csv(Path(args.output), rows, HEADERS)
    outcomes = {m.get("market_id", ""): m.get("team_a_win", "") for m in clean_rows}
    by_source = defaultdict(list)
    for row in rows:
        by_source[row.get("proxy_source", "")].append(row)
    audit = {
        "total_rows": len(rows),
        "source_counts": dict(Counter(r.get("proxy_source", "") or "missing" for r in rows)),
        "rows_by_source_universe": dict(Counter(r.get("source_universe", "") or "unknown" for r in rows)),
        "missing_price_history_by_source_universe": dict(
            Counter((r.get("source_universe", "") or "unknown") for r in rows if not r.get("proxy_source"))
        ),
        "timestamp_confidence_counts": dict(Counter(r.get("timestamp_confidence", "") for r in rows)),
        "asof_violations": sum(r.get("asof_valid") != "True" for r in rows if r.get("proxy_source")),
        "last_trade_proxy_rows": sum(r.get("proxy_source") == "last_trade_proxy" for r in rows),
        "brier_by_source": {source or "missing": brier(source_rows, outcomes) for source, source_rows in by_source.items()},
    }
    Path(args.audit_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.audit_output).write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(audit, indent=2, sort_keys=True))
    print(f"wrote {args.output}")
    print(f"wrote {args.audit_output}")


if __name__ == "__main__":
    main()
