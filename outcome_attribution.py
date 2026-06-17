"""Offline strategy outcome attribution and exit counterfactuals.

This module reads closed positions from StorageV2 raw_json rows and turns them
into a stable analytics schema. It does not affect live trading behavior.
"""
from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from typing import Any, Mapping


OUTCOME_FIELDNAMES = [
    "position_id",
    "mode",
    "match_id",
    "token_id",
    "market_name",
    "side",
    "strategy_family",
    "strategy_kind",
    "strategy_subtype",
    "signal_id",
    "entry_engine",
    "exit_engine",
    "hold_policy",
    "edge_type",
    "target_horizon",
    "expected_hold_sec",
    "entry_price",
    "exit_price",
    "shares",
    "cost_usd",
    "proceeds_usd",
    "pnl_usd",
    "roi",
    "hold_sec",
    "entry_time_ns",
    "exit_time_ns",
    "entry_game_time_sec",
    "exit_game_time_sec",
    "exit_reason",
    "entry_fair",
    "entry_edge",
    "entry_ask",
    "entry_backed_side",
    "entry_radiant_lead",
    "entry_actual_event_type",
    "entry_derived_state_flags",
    "policy_allowed",
    "policy_reason",
    "policy_version",
    "risk_tags",
    "would_pass_live",
    "live_skip_reason",
    "paper_only_bypass",
    "entry_p_game",
    "entry_series_fair",
    "entry_series_score_yes",
    "entry_series_score_no",
    "entry_current_game_number",
    "entry_market_type",
    "entry_book_age_ms",
    "settlement_price",
    "settlement_pnl_usd",
    "active_exit_delta_usd",
    "active_exit_delta_roi",
    "exit_helped",
    "settlement_status",
]


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_present(pos: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = pos.get(key)
        if value is not None and value != "":
            return value
    return None


def _csv_list(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        return ",".join(sorted(str(item) for item in value))
    return str(value)


def infer_strategy_family(
    strategy_kind: str | None,
    event_type: str | None = None,
) -> str | None:
    """Infer the top-level strategy family for current and historical rows."""
    kind = str(strategy_kind or "").upper()
    etype = str(event_type or "").upper()

    if kind == "DSWING":
        return "DSWING"
    if kind in {"VALUE", "VALUE_EDGE"}:
        return "VALUE"
    if kind in {
        "EVENT_CONTINUATION_EDGE",
        "EVENT_REVERSAL_EDGE",
        "EVENT_TRIGGERED_VALUE",
    }:
        return "EVENT"
    if kind == "BOOK_MOVE_ALPHA":
        return "BOOK_MOVE"
    if kind == "MANUAL":
        return "MANUAL"

    if etype == "DSWING":
        return "DSWING"
    if etype in {"VALUE", "VALUE_EDGE"}:
        return "VALUE"
    if etype in {
        "EVENT_CONTINUATION_EDGE",
        "EVENT_REVERSAL_EDGE",
        "EVENT_TRIGGERED_VALUE",
    }:
        return "EVENT"
    if etype == "BOOK_MOVE_ALPHA":
        return "BOOK_MOVE"
    if etype == "MANUAL":
        return "MANUAL"

    return None


def normalize_exit_reason(reason: str | None) -> str:
    if reason is None or str(reason).strip() == "":
        return "unknown"
    return str(reason).strip()


def apply_settlement_counterfactual(
    row: dict,
    settlement_price: float | None,
) -> dict:
    """Attach hold-to-settlement PnL and active-exit delta to an outcome row."""
    settlement = _as_optional_float(settlement_price)
    if settlement is None:
        row.update({
            "settlement_price": None,
            "settlement_pnl_usd": None,
            "active_exit_delta_usd": None,
            "active_exit_delta_roi": None,
            "exit_helped": None,
            "settlement_status": "unknown",
        })
        return row

    shares = _as_float(row.get("shares"))
    cost_usd = _as_float(row.get("cost_usd"))
    actual_pnl = _as_float(row.get("pnl_usd"))
    settlement_pnl = shares * settlement - cost_usd
    delta = actual_pnl - settlement_pnl
    row.update({
        "settlement_price": settlement,
        "settlement_pnl_usd": settlement_pnl,
        "active_exit_delta_usd": delta,
        "active_exit_delta_roi": delta / cost_usd if cost_usd > 0 else 0.0,
        "exit_helped": delta > 0,
        "settlement_status": "known",
    })
    return row


def closed_position_to_outcome_row(
    pos: dict,
    *,
    mode: str,
    settlement_price: float | None = None,
) -> dict:
    """Normalize one StorageV2 closed-position raw_json dict."""
    p = dict(pos or {})

    entry_ns = _as_int(p.get("entry_time_ns"))
    exit_ns = _as_int(p.get("exit_time_ns"))
    hold_sec = _as_optional_float(p.get("hold_sec"))
    if hold_sec is None and entry_ns is not None and exit_ns is not None:
        hold_sec = (exit_ns - entry_ns) / 1_000_000_000

    entry_price = _as_float(_first_present(p, "entry_price", "entry_px"))
    exit_price = _as_float(_first_present(p, "exit_price", "exit_px"))
    shares = _as_float(p.get("shares"))
    cost_usd = _as_float(_first_present(p, "cost_usd", "size_usd"))

    proceeds_usd = _as_optional_float(p.get("proceeds_usd"))
    if proceeds_usd is None:
        proceeds_usd = exit_price * shares

    pnl_usd = _as_optional_float(p.get("pnl_usd"))
    if pnl_usd is None:
        pnl_usd = proceeds_usd - cost_usd

    roi = _as_optional_float(p.get("roi"))
    if roi is None:
        roi = pnl_usd / cost_usd if cost_usd > 0 else 0.0

    strategy_kind = p.get("strategy_kind")
    strategy_family = p.get("strategy_family") or infer_strategy_family(
        strategy_kind,
        _first_present(p, "entry_actual_event_type", "event_type"),
    )
    position_id = p.get("position_id")
    if not position_id:
        position_id = f"{p.get('match_id') or ''}:{p.get('token_id') or ''}:{entry_ns or ''}"

    row = {
        "position_id": position_id,
        "mode": str(mode).lower(),
        "match_id": str(p.get("match_id") or ""),
        "token_id": str(p.get("token_id") or ""),
        "market_name": p.get("market_name"),
        "side": p.get("side"),
        "strategy_family": strategy_family,
        "strategy_kind": strategy_kind,
        "strategy_subtype": p.get("strategy_subtype"),
        "signal_id": p.get("signal_id"),
        "entry_engine": p.get("entry_engine"),
        "exit_engine": p.get("exit_engine"),
        "hold_policy": p.get("hold_policy"),
        "edge_type": p.get("edge_type"),
        "target_horizon": p.get("target_horizon"),
        "expected_hold_sec": p.get("expected_hold_sec"),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "shares": shares,
        "cost_usd": cost_usd,
        "proceeds_usd": proceeds_usd,
        "pnl_usd": pnl_usd,
        "roi": roi,
        "hold_sec": hold_sec,
        "entry_time_ns": entry_ns,
        "exit_time_ns": exit_ns,
        "entry_game_time_sec": p.get("entry_game_time_sec"),
        "exit_game_time_sec": p.get("exit_game_time_sec"),
        "exit_reason": normalize_exit_reason(p.get("exit_reason")),
        "entry_fair": _first_present(p, "entry_fair", "fair_price"),
        "entry_edge": p.get("entry_edge"),
        "entry_ask": _first_present(p, "entry_ask", "entry_price", "entry_px"),
        "entry_backed_side": _first_present(p, "entry_backed_side", "backed_side", "backed_direction"),
        "entry_radiant_lead": _first_present(p, "entry_radiant_lead", "radiant_lead", "lead"),
        "entry_actual_event_type": _first_present(p, "entry_actual_event_type", "event_type"),
        "entry_derived_state_flags": _csv_list(p.get("entry_derived_state_flags")),
        "policy_allowed": p.get("policy_allowed"),
        "policy_reason": p.get("policy_reason"),
        "policy_version": p.get("policy_version"),
        "risk_tags": _csv_list(p.get("risk_tags")),
        "would_pass_live": _first_present(p, "would_pass_live", "would_pass_live_gates"),
        "live_skip_reason": p.get("live_skip_reason"),
        "paper_only_bypass": p.get("paper_only_bypass"),
        "entry_p_game": p.get("entry_p_game"),
        "entry_series_fair": p.get("entry_series_fair"),
        "entry_series_score_yes": p.get("entry_series_score_yes"),
        "entry_series_score_no": p.get("entry_series_score_no"),
        "entry_current_game_number": p.get("entry_current_game_number"),
        "entry_market_type": p.get("entry_market_type"),
        "entry_book_age_ms": p.get("entry_book_age_ms"),
    }
    return apply_settlement_counterfactual(row, settlement_price)


def _resolve_settlement_price(
    pos: Mapping[str, Any],
    settlement_by_match: Mapping[str, Any] | None,
) -> float | None:
    if not settlement_by_match:
        return None
    match_id = str(pos.get("match_id") or "")
    if not match_id:
        return None
    settlement = settlement_by_match.get(match_id)
    if isinstance(settlement, Mapping):
        for key in (
            str(pos.get("position_id") or ""),
            str(pos.get("token_id") or ""),
            str(pos.get("side") or ""),
            "settlement_price",
            "price",
            "default",
        ):
            if key and key in settlement:
                return _as_optional_float(settlement[key])
        return None
    return _as_optional_float(settlement)


def load_strategy_outcomes(
    storage,
    *,
    modes=("paper", "live"),
    settlement_by_match: dict[str, dict] | None = None,
) -> list[dict]:
    """Load closed StorageV2 positions and normalize them into outcome rows."""
    rows = []
    for mode in modes:
        for pos in storage.load_closed_positions(mode):
            rows.append(closed_position_to_outcome_row(
                pos,
                mode=mode,
                settlement_price=_resolve_settlement_price(pos, settlement_by_match),
            ))
    return rows


def _sum_known(rows: list[dict], field: str) -> float | None:
    values = [float(row[field]) for row in rows if row.get(field) is not None]
    if not values:
        return None
    return sum(values)


def _avg_known(rows: list[dict], field: str) -> float | None:
    values = [_as_optional_float(row.get(field)) for row in rows]
    known = [value for value in values if value is not None]
    if not known:
        return None
    return sum(known) / len(known)


def summarize_strategy_outcomes(
    rows: list[dict],
    *,
    group_by=("mode", "strategy_family", "strategy_kind"),
) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[tuple(row.get(field) for field in group_by)].append(row)

    summaries = []
    for key, group_rows in groups.items():
        summary = {field: value for field, value in zip(group_by, key)}
        trades = len(group_rows)
        actual_pnl = sum(_as_float(row.get("pnl_usd")) for row in group_rows)
        settlement_pnl = _sum_known(group_rows, "settlement_pnl_usd")
        active_delta = _sum_known(group_rows, "active_exit_delta_usd")
        wins = sum(1 for row in group_rows if _as_float(row.get("pnl_usd")) > 0)
        losses = sum(1 for row in group_rows if _as_float(row.get("pnl_usd")) < 0)
        summary.update({
            "trades": trades,
            "wins": wins,
            "losses": losses,
            "win_rate": wins / trades if trades else 0.0,
            "total_cost_usd": sum(_as_float(row.get("cost_usd")) for row in group_rows),
            "actual_pnl_usd": actual_pnl,
            "settlement_pnl_usd": settlement_pnl,
            "active_exit_delta_usd": active_delta,
            "avg_active_exit_delta_usd": active_delta / trades if active_delta is not None and trades else None,
            "avg_roi": _avg_known(group_rows, "roi"),
            "avg_hold_sec": _avg_known(group_rows, "hold_sec"),
            "avg_entry_edge": _avg_known(group_rows, "entry_edge"),
            "avg_entry_ask": _avg_known(group_rows, "entry_ask"),
            "avg_entry_fair": _avg_known(group_rows, "entry_fair"),
        })
        summaries.append(summary)

    return sorted(summaries, key=lambda row: tuple(str(row.get(field) or "") for field in group_by))


def summarize_exit_reasons(rows: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[normalize_exit_reason(row.get("exit_reason"))].append(row)

    summaries = []
    for exit_reason, group_rows in groups.items():
        trades = len(group_rows)
        active_delta = _sum_known(group_rows, "active_exit_delta_usd")
        helped = [row.get("exit_helped") for row in group_rows if row.get("exit_helped") is not None]
        summaries.append({
            "exit_reason": exit_reason,
            "trades": trades,
            "wins": sum(1 for row in group_rows if _as_float(row.get("pnl_usd")) > 0),
            "actual_pnl_usd": sum(_as_float(row.get("pnl_usd")) for row in group_rows),
            "settlement_pnl_usd": _sum_known(group_rows, "settlement_pnl_usd"),
            "active_exit_delta_usd": active_delta,
            "avg_active_exit_delta_usd": active_delta / trades if active_delta is not None and trades else None,
            "exit_help_rate": sum(1 for value in helped if value) / len(helped) if helped else None,
            "avg_roi": _avg_known(group_rows, "roi"),
        })
    return sorted(summaries, key=lambda row: str(row.get("exit_reason") or ""))


def write_strategy_outcomes_csv(
    rows: list[dict],
    path: str = "logs/strategy_outcomes.csv",
) -> None:
    """Write rows with a deterministic header."""
    fieldnames = list(OUTCOME_FIELDNAMES)
    extras = sorted({key for row in rows for key in row.keys()} - set(fieldnames))
    fieldnames.extend(extras)

    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _print_strategy_summary(summary: list[dict]) -> None:
    print("\nStrategy outcomes")
    print("-" * 104)
    print(f"{'mode':<8} {'family':<12} {'kind':<24} {'trades':>6} {'win%':>7} {'actual':>10} {'settle':>10} {'delta':>10}")
    for row in summary:
        settlement = row.get("settlement_pnl_usd")
        delta = row.get("active_exit_delta_usd")
        print(
            f"{str(row.get('mode') or ''):<8} "
            f"{str(row.get('strategy_family') or ''):<12} "
            f"{str(row.get('strategy_kind') or ''):<24} "
            f"{row.get('trades', 0):>6} "
            f"{row.get('win_rate', 0.0):>7.3f} "
            f"{row.get('actual_pnl_usd', 0.0):>+10.2f} "
            f"{settlement if settlement is not None else 0.0:>+10.2f} "
            f"{delta if delta is not None else 0.0:>+10.2f}"
        )


def _print_exit_summary(summary: list[dict]) -> None:
    print("\nExit reasons")
    print("-" * 88)
    print(f"{'reason':<28} {'trades':>6} {'wins':>6} {'actual':>10} {'settle':>10} {'delta':>10} {'help%':>7}")
    for row in summary:
        settlement = row.get("settlement_pnl_usd")
        delta = row.get("active_exit_delta_usd")
        help_rate = row.get("exit_help_rate")
        print(
            f"{str(row.get('exit_reason') or ''):<28} "
            f"{row.get('trades', 0):>6} "
            f"{row.get('wins', 0):>6} "
            f"{row.get('actual_pnl_usd', 0.0):>+10.2f} "
            f"{settlement if settlement is not None else 0.0:>+10.2f} "
            f"{delta if delta is not None else 0.0:>+10.2f} "
            f"{help_rate if help_rate is not None else 0.0:>7.3f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Strategy outcome attribution")
    parser.add_argument("--db", default="logs/state_v2.sqlite", help="StorageV2 SQLite path")
    parser.add_argument("--mode", choices=["paper", "live", "all"], default="all")
    parser.add_argument("--out", default="logs/strategy_outcomes.csv", help="CSV export path")
    parser.add_argument("--summary", action="store_true", help="Print strategy and exit summaries")
    args = parser.parse_args()

    from storage_v2 import StorageV2

    modes = ("paper", "live") if args.mode == "all" else (args.mode,)
    rows = load_strategy_outcomes(StorageV2(path=args.db), modes=modes)
    write_strategy_outcomes_csv(rows, args.out)
    print(f"Exported {len(rows)} outcome rows to {args.out}")

    if args.summary:
        _print_strategy_summary(summarize_strategy_outcomes(rows))
        _print_exit_summary(summarize_exit_reasons(rows))


if __name__ == "__main__":
    main()
