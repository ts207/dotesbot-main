"""Continuous-signal scorer.

Pure function (no I/O, no side effects, no globals). Given two consecutive
Steam snapshots for the same match plus current YES and NO book state, returns
a `ContinuousSignal` to trade or `None` to skip.

Strategy provenance: derived from `scripts/snapshot_book_study.py` on
data_v2 (2,234 snapshot pairs, 13 days, 76 matches). The 185-trade variant
(hard gates + dual sizing multipliers) backtested +$0.42/trade at 64% win,
surviving up to 2c full-cross spread cost.

Gates and thresholds are encoded as module-level constants so each can be
adjusted via `.env` later without code change. The scorer itself is
deterministic and unit-testable in isolation.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Mapping

# Ensure .env is loaded before this module reads its env vars. Safe to call
# multiple times — dotenv is idempotent.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Gate thresholds (env-tunable so we can adjust live without redeploy).
# ---------------------------------------------------------------------------
GAP_MAX_SEC = float(os.getenv("CONTINUOUS_GAP_MAX_SEC", "60.0"))
MAGNITUDE_MIN = float(os.getenv("CONTINUOUS_MAGNITUDE_MIN", "1500"))
REF_BAND_LOW = float(os.getenv("CONTINUOUS_REF_BAND_LOW", "0.30"))
REF_BAND_HIGH = float(os.getenv("CONTINUOUS_REF_BAND_HIGH", "0.85"))
PHASE_MIN_SEC = int(os.getenv("CONTINUOUS_PHASE_MIN_SEC", "900"))
PHASE_MAX_SEC = int(os.getenv("CONTINUOUS_PHASE_MAX_SEC", "2700"))
BOOK_IMBALANCE_DEAD_LOW = float(os.getenv("CONTINUOUS_BOOK_IMBALANCE_DEAD_LOW", "0.20"))
BOOK_IMBALANCE_DEAD_HIGH = float(os.getenv("CONTINUOUS_BOOK_IMBALANCE_DEAD_HIGH", "0.40"))
CROSS_BOOK_DISAGREE_MAX = float(os.getenv("CONTINUOUS_CROSS_BOOK_DISAGREE_MAX", "0.02"))

# Sizing thresholds.
CONVICTION_PREGAME_MIN = float(os.getenv("CONTINUOUS_CONVICTION_PREGAME_MIN", "0.10"))
CONVICTION_LEAD_MAX = float(os.getenv("CONTINUOUS_CONVICTION_LEAD_MAX", "10000"))
CONVICTION_MULTIPLIER = float(os.getenv("CONTINUOUS_CONVICTION_MULTIPLIER", "1.5"))
MAGNITUDE_SMALL_MAX = float(os.getenv("CONTINUOUS_MAGNITUDE_SMALL_MAX", "2800"))
MAGNITUDE_LARGE_MIN = float(os.getenv("CONTINUOUS_MAGNITUDE_LARGE_MIN", "5000"))
MAGNITUDE_SMALL_MULTIPLIER = float(os.getenv("CONTINUOUS_MAGNITUDE_SMALL_MULTIPLIER", "1.5"))
MAGNITUDE_LARGE_MULTIPLIER = float(os.getenv("CONTINUOUS_MAGNITUDE_LARGE_MULTIPLIER", "0.5"))

# Execution params.
BASE_TRADE_USD = float(os.getenv("CONTINUOUS_TRADE_USD", "5.0"))
EXIT_HORIZON_SEC = int(os.getenv("CONTINUOUS_HOLD_SEC", "60"))

# Deterministic signal_id namespace (matches backfill_to_v2.py).
_NAMESPACE = uuid.UUID("11111111-2222-3333-4444-555555555555")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ContinuousSignal:
    """A scored snapshot ready for execution (or for the shadow logger)."""
    signal_id: str
    match_id: str
    received_at_ns: int
    direction: int          # +1 favors YES, -1 favors NO
    side: str               # 'YES' or 'NO' — the side to BUY
    sized_usd: float        # base * conviction_mult * magnitude_mult
    exit_horizon_sec: int
    # Pricing
    yes_mid: float
    no_mid: float
    yes_ask: float
    no_ask: float
    yes_bid: float
    no_bid: float
    ref_mid_blended: float  # (yes_mid + 1 - no_mid) / 2
    # Features (for logging + later analysis)
    game_time_sec: int
    d_lead_1: int
    d_kill_1: int
    cur_lead_yes: int
    pregame_signed: float
    book_imbalance_yes: float
    book_imbalance_no: float
    snap_gap_sec: float
    # Sizing breakdown (so the logger can audit)
    conviction_mult: float
    magnitude_mult: float


@dataclass(frozen=True)
class ScoreReject:
    """Returned instead of a signal when a gate fails. Carries the reason
    string so the shadow logger can audit which gates fired."""
    match_id: str
    received_at_ns: int
    reason: str
    # Optional partial features for forensic logging
    features: Mapping = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _signed_for_yes(value: float | int, side_mapping: str) -> float | int:
    """Snapshot fields are radiant-anchored. If YES==dire (steam_side_mapping
    == 'reversed'), flip the sign so positive = favors YES across the board."""
    return value if side_mapping == "normal" else -value


def _book_imbalance(ask_size: float | None, bid_size: float | None) -> float | None:
    """ask_size / (ask_size + bid_size). Returns None if either is missing
    or the sum is zero."""
    if ask_size is None or bid_size is None:
        return None
    s = ask_size + bid_size
    if s <= 0:
        return None
    return ask_size / s


def _make_signal_id(match_id: str, received_at_ns: int) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"continuous|{match_id}|{received_at_ns}"))


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------
def score_snapshot(
    *,
    prev_snap: Mapping,
    cur_snap: Mapping,
    yes_book: Mapping,
    no_book: Mapping,
    pregame_yes_mid: float,
    mapping: Mapping,
) -> ContinuousSignal | ScoreReject:
    """Score one snapshot pair. Returns a `ContinuousSignal` to trade or a
    `ScoreReject` with the reason a gate failed.

    Snapshot dict keys expected:
        received_at_ns, game_time_sec, radiant_lead, radiant_score, dire_score
    Book dict keys expected:
        best_bid, best_ask, mid, ask_size, bid_size

    `mapping` must carry `steam_side_mapping` ('normal' or 'reversed').
    """
    match_id = str(cur_snap.get("match_id") or prev_snap.get("match_id") or "")
    cur_ns = int(cur_snap["received_at_ns"])
    prev_ns = int(prev_snap["received_at_ns"])

    # --- Gate 1: snap freshness ---
    gap_sec = (cur_ns - prev_ns) / 1e9
    if gap_sec > GAP_MAX_SEC:
        return ScoreReject(match_id, cur_ns, "snap_gap_too_large",
                           {"snap_gap_sec": gap_sec})
    if gap_sec <= 0:
        return ScoreReject(match_id, cur_ns, "snap_gap_non_positive",
                           {"snap_gap_sec": gap_sec})

    # --- Features (signed for YES side via mapping) ---
    side_map = mapping.get("steam_side_mapping", "normal")
    d_lead_raw = int(cur_snap.get("radiant_lead") or 0) - int(prev_snap.get("radiant_lead") or 0)
    d_lead_1 = _signed_for_yes(d_lead_raw, side_map)

    d_kill_raw = (
        (int(cur_snap.get("radiant_score") or 0) - int(prev_snap.get("radiant_score") or 0))
        - (int(cur_snap.get("dire_score") or 0) - int(prev_snap.get("dire_score") or 0))
    )
    d_kill_1 = _signed_for_yes(d_kill_raw, side_map)

    cur_lead_yes = _signed_for_yes(int(cur_snap.get("radiant_lead") or 0), side_map)
    game_time = int(cur_snap.get("game_time_sec") or 0)

    # --- Gate 2: magnitude ---
    if abs(d_lead_1) < MAGNITUDE_MIN:
        return ScoreReject(match_id, cur_ns, "magnitude_below_floor",
                           {"d_lead_1": d_lead_1})

    direction = 1 if d_lead_1 > 0 else -1

    # --- Gate 3: kill_diff agreement ---
    if (d_kill_1 > 0) != (d_lead_1 > 0):
        return ScoreReject(match_id, cur_ns, "kill_diff_disagreement",
                           {"d_lead_1": d_lead_1, "d_kill_1": d_kill_1})

    # --- Book sanity + blended fair ---
    yes_mid = yes_book.get("mid")
    no_mid = no_book.get("mid")
    yes_ask = yes_book.get("best_ask")
    no_ask = no_book.get("best_ask")
    yes_bid = yes_book.get("best_bid")
    no_bid = no_book.get("best_bid")
    if None in (yes_mid, no_mid, yes_ask, no_ask, yes_bid, no_bid):
        return ScoreReject(match_id, cur_ns, "incomplete_book",
                           {"yes_book": yes_book, "no_book": no_book})

    ref_mid_blended = (yes_mid + (1.0 - no_mid)) / 2.0

    # --- Gate 4: ref-band on blended fair ---
    if not (REF_BAND_LOW <= ref_mid_blended <= REF_BAND_HIGH):
        return ScoreReject(match_id, cur_ns, "ref_band_outside",
                           {"ref_mid_blended": ref_mid_blended})

    # --- Gate 5: cross-book disagreement ---
    cross_disagree = abs(yes_mid - (1.0 - no_mid))
    if cross_disagree > CROSS_BOOK_DISAGREE_MAX:
        return ScoreReject(match_id, cur_ns, "cross_book_disagreement",
                           {"yes_mid": yes_mid, "no_mid": no_mid,
                            "disagreement": cross_disagree})

    # --- Gate 6: game phase ---
    if not (PHASE_MIN_SEC <= game_time < PHASE_MAX_SEC):
        return ScoreReject(match_id, cur_ns, "phase_outside_band",
                           {"game_time_sec": game_time})

    # --- Gate 7: book imbalance dead-zone ---
    # Use the side we will actually buy.
    side = "YES" if direction > 0 else "NO"
    if side == "YES":
        target_imb = _book_imbalance(yes_book.get("ask_size"), yes_book.get("bid_size"))
    else:
        target_imb = _book_imbalance(no_book.get("ask_size"), no_book.get("bid_size"))

    if target_imb is not None and (BOOK_IMBALANCE_DEAD_LOW <= target_imb < BOOK_IMBALANCE_DEAD_HIGH):
        return ScoreReject(match_id, cur_ns, "book_imbalance_dead_zone",
                           {"side": side, "imbalance": target_imb})

    yes_imb = _book_imbalance(yes_book.get("ask_size"), yes_book.get("bid_size")) or 0.5
    no_imb = _book_imbalance(no_book.get("ask_size"), no_book.get("bid_size")) or 0.5

    # --- Pregame signed and sizing multipliers ---
    pregame_signed = (pregame_yes_mid - 0.5) * direction
    conviction_mult = (
        CONVICTION_MULTIPLIER
        if (pregame_signed >= CONVICTION_PREGAME_MIN
            and abs(cur_lead_yes) < CONVICTION_LEAD_MAX)
        else 1.0
    )
    abs_d = abs(d_lead_1)
    if abs_d < MAGNITUDE_SMALL_MAX:
        magnitude_mult = MAGNITUDE_SMALL_MULTIPLIER
    elif abs_d < MAGNITUDE_LARGE_MIN:
        magnitude_mult = 1.0
    else:
        magnitude_mult = MAGNITUDE_LARGE_MULTIPLIER

    sized_usd = BASE_TRADE_USD * conviction_mult * magnitude_mult

    return ContinuousSignal(
        signal_id=_make_signal_id(match_id, cur_ns),
        match_id=match_id,
        received_at_ns=cur_ns,
        direction=direction,
        side=side,
        sized_usd=sized_usd,
        exit_horizon_sec=EXIT_HORIZON_SEC,
        yes_mid=yes_mid, no_mid=no_mid,
        yes_ask=yes_ask, no_ask=no_ask,
        yes_bid=yes_bid, no_bid=no_bid,
        ref_mid_blended=ref_mid_blended,
        game_time_sec=game_time,
        d_lead_1=int(d_lead_1),
        d_kill_1=int(d_kill_1),
        cur_lead_yes=int(cur_lead_yes),
        pregame_signed=pregame_signed,
        book_imbalance_yes=yes_imb,
        book_imbalance_no=no_imb,
        snap_gap_sec=gap_sec,
        conviction_mult=conviction_mult,
        magnitude_mult=magnitude_mult,
    )
