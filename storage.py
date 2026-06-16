from __future__ import annotations

import csv
import gzip
import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from typing import Iterable

_storage_logger = logging.getLogger(__name__)

from config import (
    CSV_LOG_PATH, PAPER_TRADES_CSV_PATH, DOTA_EVENTS_CSV_PATH, BOOK_EVENTS_CSV_PATH,
    LIVE_ATTEMPTS_CSV_PATH, LATENCY_CSV_PATH, LIVE_LEAGUE_RAW_JSONL_PATH,
    RICH_CONTEXT_CSV_PATH, SOURCE_DELAY_CSV_PATH,
    BOOK_REFRESH_RESCUE_CSV_PATH,
    BOOK_MOVES_CSV_PATH,
    ACTUAL_DOTA_EVENTS_CSV_PATH, LEGACY_DOTA_EVENTS_CSV_PATH, STRATEGY_SIGNALS_CSV_PATH,
    RUN_ID, CODE_VERSION, CONFIG_HASH,
)

RAW_SNAPSHOTS_CSV_PATH = "logs/raw_snapshots.csv"
SIGNAL_MARKOUTS_CSV_PATH = "logs/signal_markouts.csv"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _iso_to_ns(s: str | None) -> int | None:
    """Parse an ISO-8601 timestamp into nanoseconds since epoch."""
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(s).timestamp() * 1_000_000_000)
    except (TypeError, ValueError):
        return None


def ns_to_iso(ns: int | None) -> str | None:
    if not ns:
        return None
    return datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc).isoformat(timespec="milliseconds")


import queue
import threading


# Phase-2 dual-write: optionally tee CsvLogger appends to a unified_storage
# BatchWriter. If unified_storage import fails (e.g., missing pyarrow), we
# fall back to CSV-only — the bot keeps running.
try:
    from unified_storage import BatchWriter  # type: ignore
    _UNIFIED_AVAILABLE = True
except Exception as _e:  # pragma: no cover — exercised only on missing deps
    _UNIFIED_AVAILABLE = False
    _storage_logger.info("unified_storage unavailable (%s); CSV-only mode", _e)


class CsvLogger:
    # Opt-in size-based rotation. Subclasses pass rotate_bytes > 0 to enable.
    # Every CSV_SIZE_CHECK_EVERY writes we stat the file; when it exceeds
    # rotate_bytes, the current file is atomically renamed (with a UTC
    # microsecond suffix) and gzipped in a background thread, and a new file
    # is started with the same header.
    _SIZE_CHECK_EVERY = 1000

    def __init__(self, filename: str, headers: list[str],
                 parquet_table: str | None = None,
                 rotate_bytes: int = 0,
                 parquet_only: bool = False):
        self.filename = filename
        self.headers = headers
        self.rotate_bytes = rotate_bytes
        self._writes_since_check = 0
        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        # Parquet-only mode: skip CSV write path entirely. Only valid when a
        # parquet_table is set. Falls back to CSV if unified_storage is missing.
        self._parquet_only = bool(parquet_only and parquet_table and _UNIFIED_AVAILABLE)
        if not self._parquet_only:
            self._init_file()
        self._parquet_writer = None
        if parquet_table and _UNIFIED_AVAILABLE:
            try:
                self._parquet_writer = BatchWriter(
                    parquet_table,
                    source_file=os.path.basename(filename),
                )
            except Exception as exc:
                _storage_logger.warning(
                    "BatchWriter init failed for %s table=%s: %s — CSV-only",
                    filename, parquet_table, exc,
                )
                self._parquet_only = False  # fall back to CSV
                self._init_file()
        if not self._parquet_only:
            self._thread = threading.Thread(target=self._worker, daemon=True)
            self._thread.start()

    def _to_parquet_row(self, row: dict) -> dict:
        """Override in subclasses to transform a CSV row into a row whose keys
        match the unified-storage schema (e.g. timestamp_utc → received_at_utc,
        add received_at_ns). Default = identity."""
        return row

    def _init_file(self):
        parent = os.path.dirname(self.filename)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if os.path.exists(self.filename) and not self._header_matches():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            os.replace(self.filename, f"{self.filename}.{stamp}.bak")
        if not os.path.exists(self.filename):
            with open(self.filename, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=self.headers).writeheader()

    def _header_matches(self) -> bool:
        try:
            with open(self.filename, newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                existing = next(reader, [])
        except (OSError, StopIteration):
            return False
        return existing == self.headers

    def _maybe_rotate(self) -> None:
        """If rotate_bytes is set and current file exceeds it, rename + gzip
        in background, then create a fresh file with the header."""
        if not self.rotate_bytes:
            return
        try:
            size = os.path.getsize(self.filename)
        except OSError:
            return
        if size < self.rotate_bytes:
            return
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        rotated = f"{self.filename}.{ts}"
        try:
            os.rename(self.filename, rotated)
        except OSError as exc:
            _storage_logger.warning("CSV rotate rename failed for %s: %s",
                                    self.filename, exc)
            return
        # Re-create empty file with header so writer continues seamlessly.
        try:
            with open(self.filename, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=self.headers).writeheader()
        except OSError as exc:
            _storage_logger.warning("CSV recreate failed for %s: %s",
                                    self.filename, exc)
        _storage_logger.info("rotated %s -> %s (%.1f MB) — gzipping in background",
                             self.filename, rotated, size / (1024 * 1024))
        threading.Thread(target=_gzip_file_background, args=(rotated,),
                         daemon=True).start()

    def _worker(self):
        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                row = self._queue.get(timeout=1.0)
                with open(self.filename, "a", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=self.headers)
                    writer.writerow(row)
                self._writes_since_check += 1
                if self._writes_since_check >= self._SIZE_CHECK_EVERY:
                    self._writes_since_check = 0
                    self._maybe_rotate()
                self._queue.task_done()
            except queue.Empty:
                continue

    def append(self, row: dict):
        # CSV write — skipped in parquet_only mode.
        if not self._parquet_only:
            clean = {key: row.get(key) for key in self.headers}
            self._queue.put(clean)
        # Parquet write — best-effort, never blocks CSV.
        if self._parquet_writer is not None:
            try:
                self._parquet_writer.append(self._to_parquet_row(dict(row)))
            except Exception as exc:
                _storage_logger.warning("parquet append failed for %s: %s",
                                        self.filename, exc)

    def append_many(self, rows: Iterable[dict]):
        for row in rows:
            self.append(row)

    def stop(self):
        if not self._parquet_only:
            self._stop_event.set()
            if self._thread.is_alive():
                self._thread.join()
        if self._parquet_writer is not None:
            try:
                self._parquet_writer.close()
            except Exception as exc:
                _storage_logger.warning("parquet close failed: %s", exc)


class SignalLogger(CsvLogger):
    def __init__(self, filename: str = CSV_LOG_PATH):
        super().__init__(filename, [
            "timestamp_utc",
            "run_id", "code_version", "config_hash",
            "match_id", "lobby_id", "league_id", "radiant_team", "dire_team",
            "game_time_sec", "radiant_lead", "radiant_score", "dire_score",
            "market_name", "market_type", "yes_team", "yes_token_id",
            "event_type", "event_namespace", "cluster_event_types", "event_direction", "severity",
            "event_tier", "event_is_primary", "event_family", "event_quality",
            "event_schema_version", "snapshot_gap_sec", "actual_window_sec",
            "networth_delta", "kill_diff_delta", "total_kills_delta",
            "networth_delta_per_30s", "kill_diff_delta_per_30s", "source_cadence_quality",
            "token_id", "side",
            "lag", "expected_move", "fair_price", "executable_price", "executable_edge", "remaining_move",
            "fair_source",
            "market_move_recent", "price_lookback_sec", "pregame_move",
            "anchor_price", "current_price",
            "bid", "ask", "spread", "ask_size",
            "price_quality_score", "execution_quality_score", "trade_score",
            "target_size_usd", "size_multiplier", "phase_mult", "event_kill_lead",
            "decision", "skip_reason",
            "steam_age_ms", "estimated_game_time_sec", "source_update_age_sec", "stream_delay_s", "data_source", "book_age_ms", "book_age_at_signal_ms",
            "mapping_confidence", "mapping_errors", "team_id_match",
            "market_game_number_match", "duplicate_match_id_error",
            "slow_model_fair", "fast_event_adjustment", "hybrid_fair",
            "hybrid_confidence", "uncertainty_penalty",
            "proxy_market_type", "is_game3_match_proxy",
            "series_score_yes", "series_score_no",
            "current_game_number", "series_type",
            "structure_uncertainty_penalty",
            "would_pass_live_gates", "live_skip_reason", "paper_only_bypass",
        ], parquet_table="signals")

    def _to_parquet_row(self, row: dict) -> dict:
        # Timestamp alias handled centrally in rows_to_table; we only need
        # to synthesize the deterministic signal_id here.
        if not row.get("signal_id"):
            mid = row.get("match_id") or ""
            tok = row.get("token_id") or ""
            ns = _iso_to_ns(row.get("timestamp_utc")) or 0
            row["signal_id"] = f"{mid}|{tok}|{ns}"
        return row

    def log_signal(self, game: dict, mapping: dict, signal: dict, event_type: str = "",
                   event_direction: str = "", severity: str = "",
                   token_id: str = "", side: str = ""):
        self.append({
            "timestamp_utc": utc_now_iso(),
            "run_id": RUN_ID,
            "code_version": CODE_VERSION,
            "config_hash": CONFIG_HASH,
            "match_id": game.get("match_id"),
            "lobby_id": game.get("lobby_id"),
            "league_id": game.get("league_id"),
            "radiant_team": game.get("radiant_team"),
            "dire_team": game.get("dire_team"),
            "game_time_sec": game.get("game_time_sec"),
            "radiant_lead": game.get("radiant_lead"),
            "radiant_score": game.get("radiant_score"),
            "dire_score": game.get("dire_score"),
            "market_name": mapping.get("name"),
            "market_type": mapping.get("market_type"),
            "yes_team": mapping.get("yes_team"),
            "yes_token_id": mapping.get("yes_token_id"),
            "event_type": signal.get("event_type") or event_type,
            "event_namespace": signal.get("event_namespace") or "legacy_strategy_label",
            "cluster_event_types": signal.get("cluster_event_types"),
            "event_direction": signal.get("event_direction") or event_direction,
            "severity": severity,
            "event_tier": signal.get("event_tier"),
            "event_is_primary": signal.get("event_is_primary"),
            "event_family": signal.get("event_family"),
            "event_quality": signal.get("event_quality"),
            "event_schema_version": signal.get("event_schema_version"),
            "snapshot_gap_sec": signal.get("snapshot_gap_sec"),
            "actual_window_sec": signal.get("actual_window_sec"),
            "networth_delta": signal.get("networth_delta"),
            "kill_diff_delta": signal.get("kill_diff_delta"),
            "total_kills_delta": signal.get("total_kills_delta"),
            "networth_delta_per_30s": signal.get("networth_delta_per_30s"),
            "kill_diff_delta_per_30s": signal.get("kill_diff_delta_per_30s"),
            "source_cadence_quality": signal.get("source_cadence_quality"),
            "token_id": token_id or signal.get("token_id"),
            "side": side or signal.get("side"),
            "lag": signal.get("lag"),
            "expected_move": signal.get("expected_move"),
            "market_move_recent": signal.get("market_move_recent"),
            "fair_price": signal.get("fair_price"),
            "executable_price": signal.get("executable_price"),
            "executable_edge": signal.get("executable_edge"),
            "remaining_move": signal.get("remaining_move"),
            "fair_source": signal.get("fair_source"),
            "price_lookback_sec": signal.get("price_lookback_sec"),
            "pregame_move": signal.get("pregame_move"),
            "anchor_price": signal.get("anchor_price"),
            "current_price": signal.get("current_price"),
            "bid": signal.get("bid"),
            "ask": signal.get("ask"),
            "spread": signal.get("spread"),
            "ask_size": signal.get("ask_size"),
            "price_quality_score": signal.get("price_quality_score"),
            "execution_quality_score": signal.get("execution_quality_score"),
            "trade_score": signal.get("trade_score"),
            "target_size_usd": signal.get("target_size_usd"),
            "size_multiplier": signal.get("size_multiplier"),
            "phase_mult": signal.get("phase_mult"),
            "event_kill_lead": signal.get("event_kill_lead"),
            "decision": signal.get("decision"),
            "skip_reason": signal.get("skip_reason") or signal.get("reason"),
            "steam_age_ms": signal.get("steam_age_ms"),
            "source_update_age_sec": signal.get("source_update_age_sec"),
            "stream_delay_s": signal.get("stream_delay_s"),
            "data_source": signal.get("data_source"),
            "book_age_ms": signal.get("book_age_ms"),
            "book_age_at_signal_ms": signal.get("book_age_at_signal_ms") or signal.get("book_age_ms"),
            "mapping_confidence": signal.get("mapping_confidence") or game.get("mapping_confidence"),
            "mapping_errors": signal.get("mapping_errors") or game.get("mapping_errors"),
            "team_id_match": signal.get("team_id_match") or game.get("team_id_match"),
            "market_game_number_match": signal.get("market_game_number_match") or game.get("market_game_number_match"),
            "duplicate_match_id_error": signal.get("duplicate_match_id_error") or game.get("duplicate_match_id_error"),
            "slow_model_fair": signal.get("slow_model_fair"),
            "fast_event_adjustment": signal.get("fast_event_adjustment"),
            "hybrid_fair": signal.get("hybrid_fair"),
            "hybrid_confidence": signal.get("hybrid_confidence"),
            "uncertainty_penalty": signal.get("uncertainty_penalty"),
            "proxy_market_type": signal.get("proxy_market_type"),
            "is_game3_match_proxy": signal.get("is_game3_match_proxy"),
            "series_score_yes": signal.get("series_score_yes"),
            "series_score_no": signal.get("series_score_no"),
            "current_game_number": signal.get("current_game_number"),
            "series_type": signal.get("series_type"),
            "structure_uncertainty_penalty": signal.get("structure_uncertainty_penalty"),
            "would_pass_live_gates": signal.get("would_pass_live_gates"),
            "live_skip_reason": signal.get("live_skip_reason"),
            "paper_only_bypass": signal.get("paper_only_bypass"),
        })


class LatencyLogger(CsvLogger):
    def __init__(self, filename: str = LATENCY_CSV_PATH):
        super().__init__(filename, [
            "timestamp_utc", "run_id", "code_version", "config_hash",
            "match_id", "market_name", "event_type", "cluster_event_types",
            "event_direction", "game_time_sec", "data_source",
            "steam_received_at_ns", "steam_source_update_age_sec", "stream_delay_s",
            "event_detected_ns", "signal_eval_start_ns", "signal_evaluated_ns", "event_detection_latency_ms", "signal_eval_latency_ms",
            "token_id", "side", "book_received_at_ns", "book_age_at_signal_ms",
            "best_bid", "best_ask", "spread", "ask_size",
            "decision", "skip_reason", "fair_price", "executable_price", "executable_edge",
            "remaining_move", "fair_source", "required_edge", "lag",
            "paper_delay_ms", "paper_attempt_ns", "paper_fill_ns", "paper_entry_result",
            "paper_fill_price", "paper_entry_latency_ms",
            "live_submit_start_ns", "live_response_received_ns", "live_submit_latency_ms",
            "live_order_status", "live_reject_reason", "live_submitted_size_usd",
            "live_filled_size_usd", "live_avg_fill_price",
            "mapping_confidence", "mapping_errors", "team_id_match",
            "market_game_number_match", "duplicate_match_id_error",
            "slow_model_fair", "fast_event_adjustment", "hybrid_fair",
            "hybrid_confidence", "uncertainty_penalty",
        ], parquet_table="latency")

    def log_latency(self, row: dict):
        # Compute latencies if ns fields exist
        try:
            if row.get("event_detected_ns") and row.get("steam_received_at_ns"):
                row["event_detection_latency_ms"] = round((row["event_detected_ns"] - row["steam_received_at_ns"]) / 1_000_000, 2)
            if row.get("signal_evaluated_ns") and row.get("signal_eval_start_ns"):
                row["signal_eval_latency_ms"] = round((row["signal_evaluated_ns"] - row["signal_eval_start_ns"]) / 1_000_000, 2)
            if row.get("paper_fill_ns") and row.get("paper_attempt_ns"):
                row["paper_entry_latency_ms"] = round((row["paper_fill_ns"] - row["paper_attempt_ns"]) / 1_000_000, 2)
            if row.get("live_response_received_ns") and row.get("live_submit_start_ns"):
                row["live_submit_latency_ms"] = round((row["live_response_received_ns"] - row["live_submit_start_ns"]) / 1_000_000, 2)
        except (TypeError, ZeroDivisionError):
            pass
        
        row["timestamp_utc"] = utc_now_iso()
        row["run_id"] = row.get("run_id") or RUN_ID
        row["code_version"] = row.get("code_version") or CODE_VERSION
        row["config_hash"] = row.get("config_hash") or CONFIG_HASH
        self.append(row)


class PositionLogger(CsvLogger):
    """Logs paper trade entries and exits to a single CSV.

    Entries have action='entry' and exit_* fields empty.
    Exits have action='exit' with full P&L data.
    """

    def __init__(self, filename: str = PAPER_TRADES_CSV_PATH):
        super().__init__(filename, [
            "timestamp_utc", "action",
            "token_id", "match_id", "market_name", "side",
            "entry_price", "shares", "cost_usd",
            "event_type", "lag", "expected_move", "fair_price",
            "strategy_kind", "hold_policy", "entry_fair", "entry_edge",
            "entry_backed_side", "entry_radiant_lead", "entry_actual_event_type",
            "entry_derived_state_flags",
            "entry_game_time_sec",
            "exit_price", "proceeds_usd", "pnl_usd", "roi",
            "hold_sec", "exit_game_time_sec", "exit_reason",
        ])

    def log_entry(self, pos) -> None:
        d = pos.to_dict()
        d["timestamp_utc"] = utc_now_iso()
        d["action"] = "entry"
        if isinstance(d.get("entry_derived_state_flags"), (list, tuple, set)):
            d["entry_derived_state_flags"] = ",".join(str(v) for v in d["entry_derived_state_flags"])
        self.append(d)

    def log_exit(self, cp) -> None:
        d = cp.to_dict()
        d["timestamp_utc"] = ns_to_iso(cp.exit_time_ns) or utc_now_iso()
        d["action"] = "exit"
        if isinstance(d.get("entry_derived_state_flags"), (list, tuple, set)):
            d["entry_derived_state_flags"] = ",".join(str(v) for v in d["entry_derived_state_flags"])
        self.append(d)


class ActualDotaEventLogger(CsvLogger):
    def __init__(self, filename: str = ACTUAL_DOTA_EVENTS_CSV_PATH):
        super().__init__(filename, [
            "timestamp_utc", "run_id", "code_version", "config_hash",
            "event_id", "event_type", "match_id", "lobby_id", "league_id",
            "source", "side", "game_time_sec", "received_at_ns",
            "previous_value", "current_value", "delta", "window_sec",
            "radiant_lead_before", "radiant_lead_after",
            "radiant_score_before", "radiant_score_after",
            "dire_score_before", "dire_score_after",
            "networth_delta", "structure_team", "structure_tier",
            "source_field", "confidence", "details",
        ])

    def log_events(self, events):
        rows = []
        now = utc_now_iso()
        for event in events:
            row = event.to_dict() if hasattr(event, "to_dict") else dict(event)
            row["timestamp_utc"] = now
            row["run_id"] = RUN_ID
            row["code_version"] = CODE_VERSION
            row["config_hash"] = CONFIG_HASH
            rows.append(row)
        self.append_many(rows)


class DotaEventLogger(CsvLogger):
    def __init__(self, filename: str = LEGACY_DOTA_EVENTS_CSV_PATH):
        super().__init__(filename, [
            "timestamp_utc", "run_id", "code_version", "config_hash",
            "match_id", "lobby_id", "league_id", "mapping_name", "yes_team", "yes_token_id",
            "event_type", "event_namespace", "event_tier", "event_is_primary", "event_family", "event_quality", "event_dedupe_key",
            "event_schema_version", "snapshot_gap_sec", "actual_window_sec",
            "networth_delta", "kill_diff_delta", "total_kills_delta",
            "networth_delta_per_30s", "kill_diff_delta_per_30s", "source_cadence_quality",
            "component_event_types", "component_deltas", "component_window_sec",
            "severity", "game_time_sec", "radiant_team", "dire_team",
            "radiant_lead", "radiant_score", "dire_score", "tower_state",
            "previous_value", "current_value", "delta", "window_sec", "threshold", "direction",
            "base_pressure_score", "fight_pressure_score", "economic_pressure_score",
            "conversion_score", "event_confidence",
        ], parquet_table="dota_events")
    # _to_parquet_row removed — see BookEventLogger note above.

    def log_events(self, events):
        rows = []
        now = utc_now_iso()
        for event in events:
            row = event.to_dict() if hasattr(event, "to_dict") else dict(event)
            row["timestamp_utc"] = now
            row["run_id"] = RUN_ID
            row["code_version"] = CODE_VERSION
            row["config_hash"] = CONFIG_HASH
            row["event_namespace"] = row.get("event_namespace") or "legacy_strategy_label"
            rows.append(row)
        self.append_many(rows)


class BookEventLogger(CsvLogger):
    _ROTATE_BYTES = int(os.getenv("BOOK_EVENTS_ROTATE_BYTES", str(200 * 1024 * 1024)))

    def __init__(self, filename: str = BOOK_EVENTS_CSV_PATH):
        super().__init__(filename, [
            "timestamp_utc", "asset_id", "event_type", "best_bid", "best_ask", "bid_size", "ask_size",
            "mid", "spread", "source_event_type",
        ], parquet_table="book_ticks", rotate_bytes=self._ROTATE_BYTES)
    # _to_parquet_row removed — rows_to_table now handles the
    # timestamp_utc → received_at_utc alias automatically.

    def log_book(self, book: dict, source_event_type: str | None = None):
        bid = _to_float(book.get("best_bid"))
        ask = _to_float(book.get("best_ask"))
        spread = ask - bid if bid is not None and ask is not None else None
        mid = (ask + bid) / 2 if bid is not None and ask is not None else None
        self.append({
            "timestamp_utc": ns_to_iso(book.get("received_at_ns")) or utc_now_iso(),
            "asset_id": book.get("asset_id"),
            "event_type": "BOOK_TOP",
            "best_bid": bid,
            "best_ask": ask,
            "bid_size": book.get("bid_size"),
            "ask_size": book.get("ask_size"),
            "mid": mid,
            "spread": spread,
            "source_event_type": source_event_type,
        })


class RawSnapshotLogger(CsvLogger):
    """Logs every unique Steam API game-state snapshot with nanosecond precision.

    Only writes a row when game_time_sec advances for a given match, so the log
    records exactly when each Valve update arrived at the bot — the DLTV cadence.
    This is the ground-truth timestamp source for lag analysis in reaction_lag.py.
    """

    HEADERS = [
        "received_at_utc", "received_at_ns",
        "match_id", "lobby_id", "league_id", "server_steam_id",
        "game_time_sec", "radiant_lead",
        "radiant_score", "dire_score",
        "building_state", "tower_state",
        "roshan_respawn_timer",
        "stream_delay_s", "source_update_age_sec", "data_source", "spectators", "game_over",
        "players",
    ]

    # Rotate at 500MB. raw_snapshots.csv grew to 408MB unrotated; with dual-write
    # to parquet now active, the CSV is redundant for analytics — we keep it as
    # a fallback but bound its size.
    _ROTATE_BYTES = int(os.getenv("RAW_SNAPSHOTS_ROTATE_BYTES", str(500 * 1024 * 1024)))

    def __init__(self, filename: str = RAW_SNAPSHOTS_CSV_PATH):
        super().__init__(filename, self.HEADERS,
                         parquet_table="snapshots",
                         rotate_bytes=self._ROTATE_BYTES)
        # (match_id, game_time_sec) already written — deduplicates Valve update cadence
        self._seen: dict[str, int] = {}

    def log_game(self, game: dict) -> bool:
        """Log snapshot if game_time_sec advanced. Returns True if a row was written."""
        match_id = str(game.get("match_id") or "")
        game_time = game.get("game_time_sec")
        if not match_id or game_time is None:
            return False
        if self._seen.get(match_id) == game_time:
            return False
        self._seen[match_id] = game_time
        ns = game.get("received_at_ns")
        self.append({
            "received_at_utc": ns_to_iso(ns) or utc_now_iso(),
            "received_at_ns": ns,
            "match_id": match_id,
            "lobby_id": game.get("lobby_id"),
            "league_id": game.get("league_id"),
            "server_steam_id": game.get("server_steam_id"),
            "game_time_sec": game_time,
            "radiant_lead": game.get("radiant_lead"),
            "radiant_score": game.get("radiant_score"),
            "dire_score": game.get("dire_score"),
            "building_state": game.get("building_state"),
            "tower_state": game.get("tower_state"),
            "roshan_respawn_timer": game.get("roshan_respawn_timer"),
            "stream_delay_s": game.get("stream_delay_s"),
            "source_update_age_sec": game.get("source_update_age_sec"),
            "data_source": game.get("data_source"),
            "spectators": game.get("spectators"),
            "game_over": game.get("game_over"),
            "players": json.dumps(game.get("players", [])),
        })
        return True


def _to_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


LIVE_LEAGUE_RAW_CSV_PATH = "logs/liveleague_raw.csv"


# 2026-05-28 — Default lowered 500MB → 200MB after consolidation audit found
# liveleague_raw.jsonl had grown to 16 GB before being abandoned. The 500MB
# default was too lax for a stream that produces ~10 GB/day at peak. Override
# via env LIVELEAGUE_ROTATE_BYTES for special cases.
LIVELEAGUE_ROTATE_BYTES = int(os.getenv("LIVELEAGUE_ROTATE_BYTES", str(200 * 1024 * 1024)))


def _gzip_file_background(path: str) -> None:
    try:
        with open(path, "rb") as src, gzip.open(path + ".gz", "wb") as dst:
            shutil.copyfileobj(src, dst, length=64 * 1024 * 1024)
        os.remove(path)
    except OSError as exc:
        _storage_logger.warning("gzip rotation failed for %s: %s", path, exc)


class LiveLeagueRawLogger:
    _SIZE_CHECK_EVERY = 1000  # writes between size checks

    def __init__(self, filename: str = LIVE_LEAGUE_RAW_JSONL_PATH):
        self.filename = filename
        parent = os.path.dirname(self.filename)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._writes_since_check = 0

    def log_raw(self, raw: dict, received_at_ns: int):
        import json as _json
        self._writes_since_check += 1
        if self._writes_since_check >= self._SIZE_CHECK_EVERY:
            self._writes_since_check = 0
            self._maybe_rotate()
        row = {
            "timestamp_utc": utc_now_iso(),
            "received_at_ns": received_at_ns,
            "match_id": str(raw.get("match_id") or raw.get("lobby_id") or ""),
            "lobby_id": str(raw.get("lobby_id") or ""),
            "league_id": str(raw.get("league_id") or ""),
            "series_id": raw.get("series_id"),
            "series_type": raw.get("series_type"),
            "radiant_team": (raw.get("radiant_team") or {}).get("team_name") if isinstance(raw.get("radiant_team"), dict) else raw.get("radiant_team"),
            "dire_team": (raw.get("dire_team") or {}).get("team_name") if isinstance(raw.get("dire_team"), dict) else raw.get("dire_team"),
            "game_time_sec": int((raw.get("scoreboard") or {}).get("duration") or 0) or None if isinstance(raw.get("scoreboard"), dict) else None,
            "stream_delay_s": int(raw.get("stream_delay_s") or 0),
            "raw": raw,
        }
        with open(self.filename, "a", encoding="utf-8") as f:
            f.write(_json.dumps(row, default=str, sort_keys=True) + "\n")

    def _maybe_rotate(self) -> None:
        # Rename is atomic and instant; gzip happens off the write path.
        try:
            size = os.path.getsize(self.filename)
        except OSError:
            return
        if size < LIVELEAGUE_ROTATE_BYTES:
            return
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        rotated = f"{self.filename}.{ts}"
        try:
            os.rename(self.filename, rotated)
        except OSError as exc:
            _storage_logger.warning("rename rotation failed for %s: %s", self.filename, exc)
            return
        _storage_logger.info("rotated %s -> %s (%.1f MB) — gzipping in background",
                             self.filename, rotated, size / (1024 * 1024))
        threading.Thread(target=_gzip_file_background, args=(rotated,), daemon=True).start()

    def stop(self):
        """No-op for JSONL logger since it opens/closes on every write."""
        pass


class RichContextLogger(CsvLogger):
    def __init__(self, filename: str = RICH_CONTEXT_CSV_PATH):
        player_fields = [
            "account_id", "player_name", "hero_id", "kills", "deaths", "assists",
            "last_hits", "denies", "gold", "level", "gpm", "xpm", "net_worth",
            "item0", "item1", "item2", "item3", "item4", "item5",
            "backpack0", "backpack1", "backpack2", "neutral_item", "respawn_timer",
        ]
        player_headers = [
            f"{side}_p{idx}_{field}"
            for side in ("radiant", "dire")
            for idx in range(1, 6)
            for field in player_fields
        ]
        super().__init__(filename, [
            "timestamp_utc",
            "received_at_ns",
            "match_id",
            "lobby_id",
            "league_id",
            "series_id",
            "series_type",
            "game_time_sec",
            "radiant_team_id",
            "dire_team_id",
            "radiant_team",
            "dire_team",
            "radiant_team_name",
            "dire_team_name",
            "radiant_score",
            "dire_score",
            "score_diff",
            "radiant_tower_state",
            "dire_tower_state",
            "radiant_barracks_state",
            "dire_barracks_state",
            "radiant_net_worth",
            "dire_net_worth",
            "net_worth_diff",
            "top1_net_worth_diff",
            "top2_net_worth_diff",
            "top3_net_worth_diff",
            "level_diff",
            "gpm_diff",
            "xpm_diff",
            "gold_diff",
            "radiant_dead_count",
            "dire_dead_count",
            "dead_core_count",
            "radiant_max_respawn",
            "dire_max_respawn",
            "max_respawn_timer",
            "radiant_core_dead_count",
            "dire_core_dead_count",
            "radiant_top3_nw",
            "dire_top3_nw",
            "aegis_team",
            "aegis_holder_side",
            "aegis_holder_hero_id",
            "radiant_has_aegis",
            "dire_has_aegis",
            "realtime_stats_age_sec",
            "game_time_lag_sec",
            "realtime_context_status",
            "delayed_game_time_sec",
        ] + player_headers, parquet_table="snapshots")
        # (match_id, delayed_game_time_sec) already written — deduplicates Valve update cadence
        self._seen: dict[str, int] = {}

    def log_rich_context(self, game: dict):
        match_id = str(game.get("match_id") or "")
        delayed_gt = game.get("realtime_game_time_sec") or game.get("delayed_game_time_sec")
        
        if not match_id or delayed_gt is None:
            return
        
        # Deduplicate to avoid bloating the log with identical rows between Valve updates
        if self._seen.get(match_id) == delayed_gt:
            return
        self._seen[match_id] = delayed_gt

        # Ensure players list is flattened into row for logging
        if "players" in game and isinstance(game["players"], list):
            # Sort players by team and then slot/index to assign to p1..p5
            rad = [p for p in game["players"] if p.get("team") == 0]
            dire = [p for p in game["players"] if p.get("team") == 1]
            for side, p_list in (("radiant", rad), ("dire", dire)):
                for i, p in enumerate(p_list[:5]):
                    prefix = f"{side}_p{i+1}_"
                    game[f"{prefix}account_id"] = p.get("account_id")
                    game[f"{prefix}player_name"] = p.get("name") or p.get("player_name")
                    game[f"{prefix}hero_id"] = p.get("hero_id")
                    game[f"{prefix}kills"] = p.get("kills")
                    game[f"{prefix}deaths"] = p.get("deaths")
                    game[f"{prefix}assists"] = p.get("assists")
                    game[f"{prefix}net_worth"] = p.get("net_worth")
                    game[f"{prefix}gpm"] = p.get("gpm")
                    game[f"{prefix}xpm"] = p.get("xpm")
                    game[f"{prefix}level"] = p.get("level")

        # Pre-compute per-side tower state + net worth so the dashboard's
        # scoreboard can read radiant_tower_state / dire_tower_state /
        # radiant_net_worth / dire_net_worth directly. The bot's game dict
        # provides tower_state as a single 22-bit value (bits 0-10 radiant,
        # 11-21 dire) and radiant_lead as a signed networth diff.
        ts_val = game.get("tower_state")
        if ts_val not in (None, "") and not game.get("radiant_tower_state"):
            try:
                bits = int(float(ts_val))
                game["radiant_tower_state"] = bits & 0x7FF
                game["dire_tower_state"] = (bits >> 11) & 0x7FF
            except (TypeError, ValueError):
                pass
        rl = game.get("radiant_lead")
        if rl not in (None, ""):
            try:
                rl_int = int(float(rl))
            except (TypeError, ValueError):
                rl_int = None
            if rl_int is not None:
                game.setdefault("net_worth_diff", rl_int)
                # Synthesize plausible per-side net_worth so the dashboard's
                # rn-dn fallback also works. We only know the diff; encode it
                # as (max(rl,0), max(-rl,0)) — preserves sign + magnitude.
                if not game.get("radiant_net_worth"):
                    game["radiant_net_worth"] = max(rl_int, 0)
                if not game.get("dire_net_worth"):
                    game["dire_net_worth"] = max(-rl_int, 0)

        row = {key: game.get(key) for key in self.headers}
        row["timestamp_utc"] = utc_now_iso()

        self.append(row)


class SourceDelayLogger(CsvLogger):
    _ROTATE_BYTES = int(os.getenv("SOURCE_DELAY_ROTATE_BYTES", str(50 * 1024 * 1024)))

    def __init__(self, filename: str = SOURCE_DELAY_CSV_PATH):
        super().__init__(filename, [
            "timestamp_utc",
            "match_id",
            "lobby_id",
            "league_id",
            "realtime_game_time_sec",
            "toplive_game_time_sec",
            "game_time_lag_sec",
            "realtime_stats_age_sec",
            "realtime_context_status",
        ], parquet_table="source_delay", rotate_bytes=self._ROTATE_BYTES)

    def log_source_delay(self, row: dict):
        row["timestamp_utc"] = utc_now_iso()
        self.append(row)


class LiveAttemptLogger(CsvLogger):
    def __init__(self, filename: str = LIVE_ATTEMPTS_CSV_PATH):
        # Trader kind is derived from the CSV filename: live_attempts → "live",
        # paper_attempts → "paper". The bot constructs separate logger instances
        # per mode via filename, so this is reliable.
        kind = "paper" if "paper_attempts" in os.path.basename(filename) else "live"
        self._trader_kind = kind
        super().__init__(filename, [
            "timestamp_utc", "phase",
            "event_type", "event_direction", "token_id", "side",
            "market_name", "match_id", "game_time_sec",
            "fair_price", "best_ask", "price_cap", "edge", "lag", "spread",
            "event_quality", "event_schema_version", "source_cadence_quality",
            "book_age_ms", "steam_age_ms",
            "order_type", "submitted_size_usd", "filled_size_usd", "avg_fill_price",
            "order_status", "reason_if_rejected",
            "markout_3s", "markout_10s", "markout_30s",
            "raw_response_json",
            "trader_kind", "exit_horizon_sec", "signal_id",
        ], parquet_table="trade_attempts")

    def _to_parquet_row(self, row: dict) -> dict:
        # Timestamp alias handled centrally; only synthesize trader_kind +
        # attempt_id here.
        row["trader_kind"] = row.get("trader_kind") or self._trader_kind
        if not row.get("attempt_id"):
            mid = row.get("match_id") or ""
            tok = row.get("token_id") or ""
            ns = _iso_to_ns(row.get("timestamp_utc")) or 0
            row["attempt_id"] = f"{mid}|{tok}|{ns}|{row['trader_kind']}"
        return row

    def log_attempt(self, attempt, *, phase: str = "submit", markouts: dict | None = None) -> None:
        d = attempt.to_dict() if hasattr(attempt, "to_dict") else dict(attempt)
        d["timestamp_utc"] = utc_now_iso()
        d["phase"] = phase
        markouts = markouts or {}
        d["markout_3s"] = markouts.get("markout_3s")
        d["markout_10s"] = markouts.get("markout_10s")
        d["markout_30s"] = markouts.get("markout_30s")
        self.append(d)


class LiveExitLogger(CsvLogger):
    def __init__(self, filename: str = "logs/live_exits.csv"):
        super().__init__(filename, [
            "timestamp_utc",
            "position_id", "token_id", "match_id", "reason",
            "shares_requested", "shares_filled", "best_bid", "price_posted",
            "order_status", "reason_if_rejected",
            "submit_start_ns", "response_received_ns", "submit_latency_ms",
            "raw_response_json",
        ], parquet_table="exits")
    # _to_parquet_row removed — alias handled in rows_to_table.

    def log_exit_attempt(self, attempt) -> None:
        d = attempt.to_dict() if hasattr(attempt, "to_dict") else dict(attempt)
        d["timestamp_utc"] = utc_now_iso()
        self.append(d)

    def log_startup_heartbeat(self, code_version: str | None = None) -> None:
        """Append a self-test row at startup so an empty live_exits.csv after a
        live-trading session unambiguously means 'no exits' rather than 'writer
        is broken'."""
        now_ns = time.time_ns()
        self.append({
            "timestamp_utc": utc_now_iso(),
            "position_id": "STARTUP_HEARTBEAT",
            "token_id": "",
            "match_id": "",
            "reason": "startup_heartbeat",
            "shares_requested": 0,
            "shares_filled": 0,
            "best_bid": None,
            "price_posted": None,
            "order_status": "lifecycle",
            "reason_if_rejected": code_version or "",
            "submit_start_ns": now_ns,
            "response_received_ns": now_ns,
            "submit_latency_ms": 0,
            "raw_response_json": "",
        })

    def log_lifecycle(self, *, position, event: str, raw_response_json: str = "") -> None:
        """Audit-trail row for non-exit state transitions on a position
        (entry-zero-fill cleanup, startup stale-pending clear, GTC timeout).
        Without this, positions silently move to CLOSED with no record."""
        now_ns = time.time_ns()
        self.append({
            "timestamp_utc": utc_now_iso(),
            "position_id": getattr(position, "position_id", "") or "",
            "token_id": getattr(position, "token_id", "") or "",
            "match_id": getattr(position, "match_id", "") or "",
            "reason": event,
            "shares_requested": getattr(position, "shares", 0) or 0,
            "shares_filled": 0,
            "best_bid": None,
            "price_posted": None,
            "order_status": "lifecycle",
            "reason_if_rejected": "",
            "submit_start_ns": now_ns,
            "response_received_ns": now_ns,
            "submit_latency_ms": 0,
            "raw_response_json": raw_response_json,
        })


class BookRefreshRescueLogger(CsvLogger):
    def __init__(self, filename: str = BOOK_REFRESH_RESCUE_CSV_PATH):
        super().__init__(filename, [
            "timestamp_utc",
            "match_id",
            "event_type",
            "event_tier",
            "event_direction",
            "token_id",
            "local_book_age_ms",
            "local_bid",
            "local_ask",
            "local_spread",
            "local_ask_size",
            "refresh_request_start_ns",
            "refresh_response_ns",
            "refresh_latency_ms",
            "fresh_bid",
            "fresh_ask",
            "fresh_spread",
            "fresh_ask_size",
            "fresh_book_age_ms_if_available",
            "local_to_fresh_ask_change",
            "fresh_executable_edge",
            "fresh_remaining_move",
            "fresh_fair_source",
            "fresh_hybrid_fair",
            "fresh_decision",
            "fresh_skip_reason",
            "markout_3s",
            "markout_10s",
            "markout_30s",
        ])

    def log_rescue(self, row: dict) -> None:
        row["timestamp_utc"] = utc_now_iso()
        self.append(row)


class SignalMarkoutLogger(CsvLogger):
    def __init__(self, filename: str = SIGNAL_MARKOUTS_CSV_PATH):
        super().__init__(filename, [
            "timestamp_utc",
            "signal_timestamp_utc",
            "match_id",
            "market_name",
            "event_type",
            "event_tier",
            "event_is_primary",
            "event_direction",
            "token_id",
            "side",
            "decision",
            "skip_reason",
            "reference_price",
            "reference_bid",
            "reference_ask",
            "fair_price",
            "hybrid_fair",
            "executable_edge",
            "markout_3s",
            "markout_10s",
            "markout_30s",
            "edge_after_3s",
            "edge_after_10s",
            "edge_after_30s",
        ])

    def log_markout(self, row: dict) -> None:
        row["timestamp_utc"] = utc_now_iso()
        self.append(row)


class BookMoveLogger(CsvLogger):
    def __init__(self, filename: str = BOOK_MOVES_CSV_PATH):
        super().__init__(filename, [
            "timestamp_utc", "run_id",
            "token_id", "direction", "magnitude",
            "current_mid", "anchor_mid", "window_sec",
            "best_bid", "best_ask", "spread", "book_age_ms",
            "match_id", "market_name",
            "game_time_sec", "radiant_lead", "steam_age_ms",
            "steam_corroborated", "traded", "trade_skip_reason",
        ])

    def log(self, sig: dict) -> None:
        row = {k: sig.get(k) for k in self.headers}
        row["timestamp_utc"] = sig.get("timestamp_utc") or utc_now_iso()
        row["run_id"] = RUN_ID
        self.append(row)


class MatchWinnerSignalLogger(CsvLogger):
    def __init__(self, log_dir: str):
        filename = os.path.join(log_dir, "match_winner_signals.csv")
        headers = [
            "timestamp_utc", "timestamp_ns", "match_id", "event_type", "event_direction",
            "map_token_id", "map_bid", "map_ask", "map_book_age_ms",
            "match_token_id", "match_bid", "match_ask", "match_book_age_ms",
            "current_map_p_before", "current_map_p_after",
            "p_next_yes", "p_next_source", "neutral_p_next_yes",
            "match_fair_before", "match_fair_after", "match_fair_delta",
            "match_edge", "decision", "skip_reason"
        ]
        super().__init__(filename, headers)

    def log_match_signal(self, row: dict):
        if "timestamp_utc" not in row:
            row["timestamp_utc"] = ns_to_iso(row.get("timestamp_ns")) or utc_now_iso()
        self.append(row)


class ValueAttemptLogger(CsvLogger):
    """One row per value-engine scoring event. Records both signals
    and rejects for shadow-mode validation."""

    def __init__(self, filename: str = "logs/value_attempts.csv"):
        super().__init__(filename, [
            "timestamp_utc", "received_at_ns", "signal_id", "match_id",
            "would_trade", "reject_reason",
            "direction", "side", "token_id",
            "fair_price", "ask", "edge",
            "lead", "game_time_sec", "elo_diff",
            "book_age_ms", "sized_usd"
        ], parquet_table="value_attempts")

    def log_signal(self, sig) -> None:
        self.append({
            "timestamp_utc": ns_to_iso(sig.received_at_ns) or utc_now_iso(),
            "received_at_ns": sig.received_at_ns,
            "signal_id": sig.signal_id,
            "match_id": sig.match_id,
            "would_trade": True,
            "reject_reason": "",
            "direction": sig.direction,
            "side": sig.side,
            "token_id": sig.token_id,
            "fair_price": sig.fair_price,
            "ask": sig.ask,
            "edge": sig.edge,
            "lead": sig.lead,
            "game_time_sec": sig.game_time_sec,
            "elo_diff": sig.elo_diff,
            "book_age_ms": sig.book_age_ms,
            "sized_usd": sig.sized_usd,
        })

    def log_reject(self, rej) -> None:
        self.append({
            "timestamp_utc": ns_to_iso(rej.received_at_ns) or utc_now_iso(),
            "received_at_ns": rej.received_at_ns,
            "signal_id": "",
            "match_id": rej.match_id,
            "would_trade": False,
            "reject_reason": rej.reason,
            "direction": rej.direction,
            "side": rej.side,
            "token_id": rej.token_id,
            "fair_price": rej.fair_price,
            "ask": rej.ask,
            "edge": rej.edge,
            "lead": rej.lead,
            "game_time_sec": rej.game_time_sec,
            "elo_diff": rej.elo_diff,
            "book_age_ms": rej.book_age_ms,
            "sized_usd": None,
        })


class DSwingAttemptLogger(CsvLogger):
    """One row per decisive-swing scoring event."""

    def __init__(self, filename: str = "logs/dswing_attempts.csv"):
        super().__init__(filename, [
            "timestamp_utc", "received_at_ns", "signal_id", "match_id",
            "would_trade", "reject_reason",
            "market_name", "market_type", "direction", "side", "token_id",
            "lead", "game_time_sec", "p_game", "series_fair",
            "ask", "edge", "book_age_ms", "sized_usd",
        ])

    def log_signal(self, sig, *, mapping: dict | None = None) -> None:
        self.append({
            "timestamp_utc": ns_to_iso(sig.received_at_ns) or utc_now_iso(),
            "received_at_ns": sig.received_at_ns,
            "signal_id": sig.signal_id,
            "match_id": sig.match_id,
            "would_trade": True,
            "reject_reason": "",
            "market_name": (mapping or {}).get("name"),
            "market_type": (mapping or {}).get("market_type"),
            "direction": sig.direction,
            "side": sig.side,
            "token_id": sig.token_id,
            "lead": sig.lead,
            "game_time_sec": sig.game_time_sec,
            "p_game": sig.p_game,
            "series_fair": sig.series_fair,
            "ask": sig.ask,
            "edge": sig.edge,
            "book_age_ms": sig.book_age_ms,
            "sized_usd": sig.sized_usd,
        })

    def log_reject(self, rej, *, mapping: dict | None = None) -> None:
        self.append({
            "timestamp_utc": utc_now_iso(),
            "received_at_ns": None,
            "signal_id": "",
            "match_id": getattr(rej, "match_id", ""),
            "would_trade": False,
            "reject_reason": getattr(rej, "reason", ""),
            "market_name": (mapping or {}).get("name"),
            "market_type": (mapping or {}).get("market_type"),
        })


class StrategySignalLogger(CsvLogger):
    """Unified strategy-decision sidecar for new paper strategies."""

    def __init__(self, filename: str = STRATEGY_SIGNALS_CSV_PATH):
        super().__init__(filename, [
            "timestamp_utc", "received_at_ns", "signal_id", "event_id",
            "strategy", "actual_event_type", "match_id",
            "would_trade", "reject_reason",
            "direction", "side", "token_id",
            "fair_before", "fair_after", "fair_price", "fair_delta",
            "ask", "edge", "lead", "game_time_sec", "elo_diff",
            "book_age_ms", "sized_usd", "derived_state_flags",
            "is_continuation", "is_reversal",
            "would_pass_live_gates", "live_skip_reason", "paper_only_bypass",
        ])

    def log_signal(self, sig, *, strategy: str = "EVENT_TRIGGERED_VALUE") -> None:
        self.append({
            "timestamp_utc": ns_to_iso(sig.received_at_ns) or utc_now_iso(),
            "received_at_ns": sig.received_at_ns,
            "signal_id": sig.signal_id,
            "event_id": getattr(sig, "event_id", ""),
            "strategy": strategy,
            "actual_event_type": getattr(sig, "actual_event_type", ""),
            "match_id": sig.match_id,
            "would_trade": True,
            "reject_reason": "",
            "direction": getattr(sig, "direction", ""),
            "side": getattr(sig, "side", ""),
            "token_id": getattr(sig, "token_id", ""),
            "fair_before": getattr(sig, "fair_before", ""),
            "fair_after": getattr(sig, "fair_after", ""),
            "fair_price": getattr(sig, "fair_price", ""),
            "fair_delta": getattr(sig, "fair_delta", ""),
            "ask": getattr(sig, "ask", ""),
            "edge": getattr(sig, "edge", ""),
            "lead": getattr(sig, "lead", ""),
            "game_time_sec": getattr(sig, "game_time_sec", ""),
            "elo_diff": getattr(sig, "elo_diff", ""),
            "book_age_ms": getattr(sig, "book_age_ms", ""),
            "sized_usd": getattr(sig, "sized_usd", ""),
            "derived_state_flags": ",".join(getattr(sig, "derived_state_flags", [])),
            "is_continuation": getattr(sig, "is_continuation", ""),
            "is_reversal": getattr(sig, "is_reversal", ""),
            "would_pass_live_gates": getattr(sig, "would_pass_live_gates", ""),
            "live_skip_reason": getattr(sig, "live_skip_reason", ""),
            "paper_only_bypass": getattr(sig, "paper_only_bypass", ""),
        })

    def log_reject(self, rej, *, strategy: str = "EVENT_TRIGGERED_VALUE") -> None:
        self.append({
            "timestamp_utc": ns_to_iso(rej.received_at_ns) or utc_now_iso(),
            "received_at_ns": rej.received_at_ns,
            "signal_id": "",
            "event_id": rej.event_id,
            "strategy": strategy,
            "actual_event_type": rej.actual_event_type,
            "match_id": rej.match_id,
            "would_trade": False,
            "reject_reason": rej.reason,
            "direction": rej.direction,
            "side": rej.side,
            "token_id": rej.token_id,
            "fair_before": rej.fair_before,
            "fair_after": rej.fair_after,
            "fair_price": getattr(rej, "fair_price", getattr(rej, "fair_after", None)),
            "fair_delta": rej.fair_delta,
            "ask": rej.ask,
            "edge": rej.edge,
            "lead": rej.lead,
            "game_time_sec": rej.game_time_sec,
            "elo_diff": rej.elo_diff,
            "book_age_ms": rej.book_age_ms,
            "sized_usd": None,
            "derived_state_flags": None,
            "is_continuation": (
                None if getattr(rej, "is_reversal", None) is None
                else not getattr(rej, "is_reversal")
            ),
            "is_reversal": getattr(rej, "is_reversal", ""),
            "would_pass_live_gates": False,
            "live_skip_reason": "",
            "paper_only_bypass": False,
        })
