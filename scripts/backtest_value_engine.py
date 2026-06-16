#!/usr/bin/env python3
"""Backtest VALUE as a hold-to-settlement strategy.

Inputs:
- data_v2/snapshots: top_live game states.
- data_v2/book_ticks: executable best_ask at entry.
- markets.yaml: token/team mapping.
- local outcome caches first, final book-mid fallback second.

This script intentionally does not model exits. VALUE buys one token and scores
the position at settlement: winning token pays 1, losing token pays 0.
"""
from __future__ import annotations

import bisect
import csv
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as pds
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except Exception:
    pass

try:
    from market_scope import is_game3_match_proxy
except Exception:

    def is_game3_match_proxy(mapping: dict) -> bool:
        return False

import winprob


def _boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return None


def _params() -> dict[str, float | int]:
    return {
        "min_edge": float(os.getenv("VALUE_MIN_EDGE", "0.10")),
        "min_fair": float(os.getenv("VALUE_MIN_FAIR", "0.70")),
        "min_lead": int(os.getenv("VALUE_MIN_NW_LEAD", "0")),
        "min_time": int(os.getenv("VALUE_MIN_GAME_TIME", "600")),
        "max_price": float(os.getenv("VALUE_MAX_PRICE", "0.84")),
        "min_price": float(os.getenv("VALUE_MIN_PRICE", "0.0")),
        "max_edge": float(os.getenv("VALUE_MAX_EDGE", "1.0")),
        "max_time": int(os.getenv("VALUE_MAX_GAME_TIME", "99999")),
        "book_age_ms": int(os.getenv("VALUE_MAX_BOOK_AGE_MS", "15000")),
        "flip_lead": int(os.getenv("VALUE_FLIP_LEAD", "5000")),
        "flip_ask_floor": float(os.getenv("VALUE_FLIP_ASK_FLOOR", "0.35")),
        "stake": float(os.getenv("VALUE_LIVE_MAX_USD", os.getenv("VALUE_TRADE_USD", "20"))),
    }


def _confirm_params() -> dict[str, float | int]:
    return {
        "min_edge": float(os.getenv("VALUE_CONFIRM_MIN_EDGE", "0.12")),
        "max_age_ns": int(float(os.getenv("VALUE_CONFIRM_MAX_AGE_SEC", "90")) * 1_000_000_000),
        "max_ask_worsen": float(os.getenv("VALUE_CONFIRM_MAX_ASK_WORSEN", "0.02")),
    }


def load_outcomes() -> tuple[dict[str, bool], dict[str, str]]:
    """Return match_id -> radiant_win and match_id -> source."""
    outcomes: dict[str, bool] = {}
    sources: dict[str, str] = {}

    for rel, source in [
        ("logs/opendota_outcomes.json", "opendota_outcomes"),
        ("logs/opendota_match_details.json", "opendota_match_details"),
        ("logs/shadow_outcomes_cache.json", "shadow_cache"),
    ]:
        path = REPO_ROOT / rel
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        for match_id, raw in data.items():
            radiant_win = _boolish(raw.get("radiant_win") if isinstance(raw, dict) else raw)
            if radiant_win is not None and str(match_id) not in outcomes:
                outcomes[str(match_id)] = radiant_win
                sources[str(match_id)] = source

    clean_matches = REPO_ROOT / "data" / "clean" / "matches.csv"
    if clean_matches.exists():
        with clean_matches.open() as f:
            for row in csv.DictReader(f):
                match_id = str(row.get("match_id") or "")
                radiant_win = _boolish(row.get("outcome_radiant_won"))
                if match_id and radiant_win is not None and match_id not in outcomes:
                    outcomes[match_id] = radiant_win
                    sources[match_id] = "clean_matches"

    return outcomes, sources


def load_markets() -> tuple[dict[str, dict], Counter]:
    with (REPO_ROOT / "markets.yaml").open() as f:
        data = yaml.safe_load(f)

    markets: dict[str, dict] = {}
    skipped: Counter = Counter()
    for mapping in data.get("markets", []):
        match_id = str(mapping.get("dota_match_id") or "")
        if not match_id.isdigit() or match_id == "123":
            skipped["unmapped"] += 1
            continue
        if not mapping.get("yes_token_id") or not mapping.get("no_token_id"):
            skipped["missing_tokens"] += 1
            continue
        market_type = str(mapping.get("market_type") or "").upper()
        if market_type == "MATCH_WINNER" and not is_game3_match_proxy(mapping):
            skipped["series_non_proxy"] += 1
            continue
        if market_type not in {"MAP_WINNER", "MATCH_WINNER"}:
            skipped["unsupported_market_type"] += 1
            continue
        if match_id in markets:
            skipped["duplicate_match_id_later_mapping"] += 1
            continue
        markets[match_id] = mapping
    return markets, skipped


def parquet_dataset_nonempty(path: Path) -> pds.Dataset:
    files = [str(p) for p in path.rglob("*.parquet") if p.stat().st_size > 0]
    if not files:
        raise FileNotFoundError(f"no non-empty parquet files under {path}")
    return pds.dataset(files, format="parquet", partitioning="hive")


def load_snapshots(match_ids: set[str]) -> dict[str, list[dict]]:
    dataset = parquet_dataset_nonempty(REPO_ROOT / "data_v2" / "snapshots")
    columns = [
        "match_id",
        "received_at_ns",
        "received_at_utc",
        "game_time_sec",
        "radiant_lead",
        "game_over",
        "data_source",
        "radiant_team_id",
        "dire_team_id",
        "radiant_team",
        "dire_team",
        "date",
    ]
    table = dataset.to_table(
        columns=columns,
        filter=(pc.field("data_source") == "top_live")
        & pc.is_in(pc.field("match_id"), pa.array(list(match_ids))),
    )
    by_match: dict[str, list[dict]] = defaultdict(list)
    for row in table.to_pylist():
        by_match[str(row["match_id"])].append(row)
    for rows in by_match.values():
        rows.sort(key=lambda row: row.get("received_at_ns") or 0)
    return by_match


def load_books(tokens: set[str]) -> dict[str, tuple[list[int], list[dict]]]:
    dataset = parquet_dataset_nonempty(REPO_ROOT / "data_v2" / "book_ticks")
    table = dataset.to_table(
        columns=["asset_id", "received_at_ns", "best_ask", "mid", "date"],
        filter=pc.is_in(pc.field("asset_id"), pa.array(list(tokens))),
    )
    by_asset: dict[str, list[dict]] = defaultdict(list)
    for row in table.to_pylist():
        if row.get("received_at_ns") is not None:
            by_asset[str(row["asset_id"])].append(row)

    out: dict[str, tuple[list[int], list[dict]]] = {}
    for asset_id, rows in by_asset.items():
        rows.sort(key=lambda row: row["received_at_ns"])
        out[asset_id] = ([row["received_at_ns"] for row in rows], rows)
    return out


def book_at(book: dict[str, tuple[list[int], list[dict]]], token_id: str, ns: int) -> dict | None:
    item = book.get(str(token_id))
    if not item:
        return None
    times, rows = item
    idx = bisect.bisect_right(times, ns) - 1
    if idx < 0:
        return None
    return rows[idx]


def final_book_yes_won(book: dict[str, tuple[list[int], list[dict]]], yes_token_id: str) -> tuple[int | None, float | None]:
    item = book.get(str(yes_token_id))
    if not item:
        return None, None
    for row in reversed(item[1]):
        mid = row.get("mid")
        if mid is None:
            continue
        mid = float(mid)
        if mid > 0.90:
            return 1, mid
        if mid < 0.10:
            return 0, mid
        return None, mid
    return None, None


def yes_from_radiant(mapping: dict, radiant_win: bool) -> int | None:
    side_map = mapping.get("steam_side_mapping", "normal")
    if side_map == "normal":
        return 1 if radiant_win else 0
    if side_map == "reversed":
        return 0 if radiant_win else 1
    return None


def resolve_yes_won(
    match_id: str,
    mapping: dict,
    book: dict[str, tuple[list[int], list[dict]]],
    outcomes: dict[str, bool],
    outcome_sources: dict[str, str],
) -> tuple[int | None, str]:
    if match_id in outcomes:
        yes_won = yes_from_radiant(mapping, outcomes[match_id])
        if yes_won is not None:
            return yes_won, outcome_sources[match_id]

    yes_won, final_mid = final_book_yes_won(book, str(mapping["yes_token_id"]))
    if yes_won is not None:
        return yes_won, "final_book_mid"
    return None, f"unresolved_book_mid={final_mid}"


def signal_side(mapping: dict, lead: int) -> tuple[str | None, str]:
    direction = "radiant" if lead > 0 else "dire"
    side_map = mapping.get("steam_side_mapping", "normal")
    if side_map == "normal":
        return ("YES" if direction == "radiant" else "NO"), direction
    if side_map == "reversed":
        return ("NO" if direction == "radiant" else "YES"), direction
    return None, direction


def fair_price(row: dict, direction: str, lead: int, history: deque[tuple[int, int]]) -> float:
    ns = int(row.get("received_at_ns") or 0)
    target = ns - 300_000_000_000
    past = None
    for hist_ns, hist_lead in history:
        if hist_ns <= target:
            past = hist_lead
        else:
            break
    slope_rad = 0.0 if past is None else float(lead - past)

    if direction == "radiant":
        elo_diff = winprob.elo_diff(
            row.get("radiant_team_id"),
            row.get("dire_team_id"),
            row.get("radiant_team"),
            row.get("dire_team"),
        )
        slope = slope_rad
    else:
        elo_diff = winprob.elo_diff(
            row.get("dire_team_id"),
            row.get("radiant_team_id"),
            row.get("dire_team"),
            row.get("radiant_team"),
        )
        slope = -slope_rad
    return winprob.fair(abs(lead), int(row["game_time_sec"]), elo_diff, slope, None)


def replay(
    *,
    snapshots: dict[str, list[dict]],
    markets: dict[str, dict],
    book: dict[str, tuple[list[int], list[dict]]],
    outcomes: dict[str, bool],
    outcome_sources: dict[str, str],
    params: dict[str, float | int],
    confirm: bool,
) -> tuple[list[dict], Counter, list[tuple[str, str, str]], int, Counter]:
    confirm_params = _confirm_params()
    trades: list[dict] = []
    coverage: Counter = Counter()
    unresolved: list[tuple[str, str, str]] = []
    raw_signals = 0
    rejects: Counter = Counter()

    for match_id, rows in snapshots.items():
        mapping = markets[match_id]
        yes_token = str(mapping["yes_token_id"])
        no_token = str(mapping["no_token_id"])
        yes_won, source = resolve_yes_won(match_id, mapping, book, outcomes, outcome_sources)
        if yes_won is None:
            unresolved.append((match_id, source, mapping.get("name", "")))
            continue

        coverage[source] += 1
        entered = False
        armed: dict[str, dict] = {}
        history: deque[tuple[int, int]] = deque(maxlen=4000)

        for row in rows:
            if entered:
                break
            ns = int(row.get("received_at_ns") or 0)
            game_time = row.get("game_time_sec")
            lead = row.get("radiant_lead")
            if row.get("game_over") or game_time is None or lead is None:
                continue
            if game_time < params["min_time"]:
                rejects["game_too_early"] += 1
                continue
            if game_time > params["max_time"]:
                rejects["game_too_late"] += 1
                continue

            lead = int(lead)
            history.append((ns, lead))
            if abs(lead) < params["min_lead"]:
                rejects["lead_too_small"] += 1
                continue

            side, direction = signal_side(mapping, lead)
            if side is None:
                rejects["unknown_side_mapping"] += 1
                continue
            token = yes_token if side == "YES" else no_token
            entry_book = book_at(book, token, ns)
            if not entry_book:
                rejects["missing_book"] += 1
                continue
            book_age_ms = (ns - int(entry_book["received_at_ns"])) / 1_000_000
            if book_age_ms > params["book_age_ms"]:
                rejects["book_stale"] += 1
                continue

            ask = entry_book.get("best_ask")
            if ask is None or (isinstance(ask, float) and math.isnan(ask)):
                rejects["missing_ask"] += 1
                continue
            ask = float(ask)
            if ask > params["max_price"]:
                rejects["price_too_high"] += 1
                continue
            if ask < params["min_price"]:
                rejects["price_too_low"] += 1
                continue
            if abs(lead) > params["flip_lead"] and ask < params["flip_ask_floor"]:
                rejects["orientation_flip"] += 1
                continue

            fair = fair_price(row, direction, lead, history)
            edge = fair - ask
            if fair < params["min_fair"]:
                rejects["fair_too_low"] += 1
                continue
            if edge < params["min_edge"]:
                rejects["edge_too_small"] += 1
                continue
            if edge > params["max_edge"]:
                rejects["edge_too_large"] += 1
                continue

            raw_signals += 1
            if confirm:
                key = f"{match_id}|{token}|{side}"
                if edge < confirm_params["min_edge"]:
                    armed.pop(key, None)
                    rejects["confirm_edge_low"] += 1
                    continue
                prior = armed.get(key)
                if (
                    prior is None
                    or ns - prior["ns"] > confirm_params["max_age_ns"]
                    or ask > prior["ask"] + confirm_params["max_ask_worsen"]
                ):
                    armed[key] = {"ns": ns, "ask": ask}
                    rejects["confirm_wait"] += 1
                    continue

            token_won = yes_won if token == yes_token else 1 - yes_won
            stake = float(params["stake"])
            pnl = ((1.0 if token_won else 0.0) - ask) / ask * stake
            trades.append(
                {
                    "date": row.get("date"),
                    "decision_ts": ns,
                    "decision_utc": row.get("received_at_utc"),
                    "token_id": token,
                    "entry_book_ts": int(entry_book["received_at_ns"]),
                    "book_age_ms": book_age_ms,
                    "match_id": match_id,
                    "name": mapping.get("name", ""),
                    "side": side,
                    "won": int(token_won),
                    "ask": ask,
                    "fair": fair,
                    "edge": edge,
                    "lead": lead,
                    "game_time": game_time,
                    "pnl": pnl,
                    "stake": stake,
                    "outcome_source": source,
                }
            )
            entered = True

    return trades, coverage, unresolved, raw_signals, rejects


def print_summary(label: str, trades: list[dict], coverage: Counter, unresolved: list, raw_signals: int, rejects: Counter) -> None:
    n = len(trades)
    wins = sum(t["won"] for t in trades)
    pnl = sum(t["pnl"] for t in trades)
    stake = sum(t["stake"] for t in trades)
    roi = pnl / stake * 100 if stake else 0.0

    print(f"\n{label}")
    print(
        f"coverage_resolved_matches={sum(coverage.values())} "
        f"unresolved_matches={len(unresolved)} sources={dict(coverage)} raw_signals={raw_signals}"
    )
    print(
        f"trades={n} wins={wins}/{n} win_pct={(wins / n * 100 if n else 0):.1f}% "
        f"pnl=${pnl:+.2f} stake=${stake:.2f} roi={roi:+.1f}% avg=${(pnl / n if n else 0):+.2f}"
    )
    print(f"top_rejects={rejects.most_common(10)}")
    for trade in trades[:40]:
        print(
            f"  {trade['date']} {trade['match_id']} {trade['side']} won={trade['won']} "
            f"ask={trade['ask']:.3f} fair={trade['fair']:.3f} edge={trade['edge']:.3f} "
            f"lead={trade['lead']} gt={trade['game_time']} pnl=${trade['pnl']:+.2f} "
            f"src={trade['outcome_source']} {trade['name'][:70]}"
        )


def main() -> None:
    started = time.time()
    params = _params()
    outcomes, outcome_sources = load_outcomes()
    markets, skipped = load_markets()
    snapshots = load_snapshots(set(markets))

    tokens: set[str] = set()
    for match_id in snapshots:
        tokens.add(str(markets[match_id]["yes_token_id"]))
        tokens.add(str(markets[match_id]["no_token_id"]))
    book = load_books(tokens)
    joined = {
        match_id: rows
        for match_id, rows in snapshots.items()
        if str(markets[match_id]["yes_token_id"]) in book
        and str(markets[match_id]["no_token_id"]) in book
    }

    print("VALUE SETTLEMENT BACKTEST")
    print(f"params={params}")
    print(
        f"outcome_labels={len(outcomes)} valid_markets={len(markets)} skipped={dict(skipped)} "
        f"snapshot_matches={len(snapshots)} joined_matches={len(joined)} "
        f"snapshot_rows={sum(len(v) for v in joined.values())} "
        f"book_ticks={sum(len(v[0]) for v in book.values())} load_sec={time.time() - started:.1f}"
    )

    for label, confirm in [
        ("current env, no confirmation", False),
        ("current env + confirmation", True),
    ]:
        print_summary(
            label,
            *replay(
                snapshots=joined,
                markets=markets,
                book=book,
                outcomes=outcomes,
                outcome_sources=outcome_sources,
                params=params,
                confirm=confirm,
            ),
        )


if __name__ == "__main__":
    main()
