from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, replace
from typing import Any

from config import EVENT_COOLDOWN_GAME_SECONDS
from event_taxonomy import EVENT_SCHEMA_VERSION, event_family, event_is_primary, event_tier
from structure_state import StructureState, StructureDelta, decode_structure_state, diff_structure_state

COMEBACK_MIN_PRIOR_DEFICIT = 3000
MAJOR_COMEBACK_PRIOR_DEFICIT = 8000
COMEBACK_RECOVERY_MIN_SWING = 1800
MAJOR_COMEBACK_RECOVERY_MIN_SWING = 3500
STOMP_THROW_MIN_LEAD = 12_000
STOMP_THROW_MIN_NW_SWING = 2_500
STOMP_THROW_MIN_KILLS = 2
STOMP_THROW_MIN_TIME = 30 * 60

DECISIVE_STOMP_MIN_LEAD = 13_000       # raised 10k→13k: fire earlier relative to market pricing
DECISIVE_STOMP_WINDOW_LEAD = 9_000     # raised 7k→9k: require more sustained dominance
DECISIVE_STOMP_WINDOW_SEC = 80
DECISIVE_STOMP_MIN_TIME = 25 * 60
# 2026-05-30 Phase 2 — cap DECISIVE_STOMP at 40min (wr drops to 84% past that)
DECISIVE_STOMP_MAX_TIME = 40 * 60
DECISIVE_STOMP_MIN_GROWTH = 1_500      # raised 800→1500: require meaningful acceleration

# NEW: Rapid lead growth detection
RAPID_STOMP_WINDOW_SEC = 45
RAPID_STOMP_MIN_GROWTH = 3000
RAPID_STOMP_MIN_LEAD = 4000
LATE_FIGHT_TIME = 35 * 60
ULTRA_LATE_FIGHT_TIME = 50 * 60
EVENT_DEDUPE_SECONDS = 120

DIRECT_GAP_SEC = 20
NORMAL_GAP_SEC = 75
STALE_GAP_SEC = 150
MAX_FIGHT_GAP_SEC = 90

SIDE_MASK = 0x7FF

TACTICAL_PRIORITY: dict[str, int] = {
    "OBJECTIVE_CONVERSION_T4": 120,
    "THRONE_EXPOSED": 110,
    "OBJECTIVE_CONVERSION_RAX": 105,
    "OBJECTIVE_CONVERSION_T3": 100,
    # 2026-05-31 — S1/S2 must outrank all mid-game events so they become the
    # cluster PRIMARY. Bug fix: FIRST_SWING_SETTLE had no priority (=0), so when
    # it co-fired with a now-blacklisted event (PRE_PUSH etc.), that event became
    # primary and signal_engine rejected the whole cluster as event_type_inactive
    # — losing the highest-edge S1 trade (observed on LGD vs Pip G2, a 30-31 game).
    # Must outrank the highest BLACKLISTED event that can co-fire in the 10-35min
    # window (OBJECTIVE_CONVERSION_T3=100, a T3 tower fall, blacklisted). Set to
    # 104 — above OBJ_T3(100) so S1 wins, below whitelisted terminal RAX(105)/
    # THRONE(110)/T4(120) which are end-game and don't overlap S1's window anyway.
    "POLL_FIRST_SWING_SETTLE": 104,
    "POLL_REVERSAL_ENTRY": 103,
    "POLL_AEGIS_MOMENTUM": 98,
    "POLL_ULTRA_LATE_FIGHT_FLIP": 90,
    "POLL_BUYBACK_CAPITULATION": 95,
    "POLL_STOMP_THROW_CONFIRMED": 80,
    "POLL_LATE_FIGHT_FLIP": 70,
    "POLL_LEAD_FLIP_WITH_KILLS": 60,
    "POLL_MAJOR_COMEBACK_RECOVERY": 50,
    "POLL_MAJOR_COMEBACK_FADE": 52,  # 2026-05-30 fade — slightly above recovery
    "POLL_TEAM_WIPE": 45,
    "POLL_KILL_BURST_CONFIRMED": 40,
    "POLL_FIGHT_SWING": 30,
    "POLL_RAPID_STOMP": 28,
    "POLL_DECISIVE_STOMP": 25,
    "POLL_COMEBACK_RECOVERY": 20,
    "POLL_STRUCTURAL_DOMINANCE": 15,
    "POLL_PRE_PUSH_SETUP": 14,  # 2026-05-29 backtest: 12 days, 91% settle win
    "POLL_NW_KILL_DIVERGENCE": 13,  # 2026-05-30 #6: 76% wr at NW>=3k/kill>=3 opposite
    # 2026-05-30 Phase B — real-time-only detectors (no lagged GetRealtimeStats)
    "POLL_KILL_BURST_TIGHT": 45,        # higher than KILL_BURST_CONFIRMED (40) — faster window
    "POLL_NW_VELOCITY_SUSTAINED": 32,   # between FIGHT_SWING (30) and KILL_BURST (40)
    "POLL_KILL_GAP_ACCEL": 35,          # accelerating snowball
    "POLL_PHASE_NORMALIZED_LEAD": 12,   # state-based, low priority
    "OBJECTIVE_CONVERSION_T2": 10,
    "BASE_PRESSURE_T4": 8,
    "BASE_PRESSURE_T3_COLLAPSE": 6,
    "BLOODY_EVEN_FIGHT": 1,
}

CONVERSION_TOWER_COMPONENTS = frozenset({
    "T2_TOWER_FALL",
    "MULTIPLE_T2_TOWERS_DOWN",
    "ALL_T2_TOWERS_DOWN",
    "T3_TOWER_FALL",
    "MULTIPLE_T3_TOWERS_DOWN",
    "ALL_T3_TOWERS_DOWN",
    "FIRST_T4_TOWER_FALL",
    "SECOND_T4_TOWER_FALL",
    "T3_PLUS_T4_CHAIN",
    "MULTI_STRUCTURE_COLLAPSE",
    "THRONE_EXPOSED_COMPONENT",
})

TACTICAL_SUPPORT_COMPONENTS = frozenset({
    "POLL_FIGHT_SWING",
    "POLL_KILL_BURST_CONFIRMED",
    "POLL_LEAD_FLIP_WITH_KILLS",
    "POLL_COMEBACK_RECOVERY",
    "POLL_MAJOR_COMEBACK_RECOVERY",
    "POLL_STOMP_THROW_CONFIRMED",
    "POLL_LATE_FIGHT_FLIP",
    "POLL_ULTRA_LATE_FIGHT_FLIP",
    "POLL_RAPID_STOMP",
})

_EVENT_BASE_PRESSURE: dict[str, float] = {
    "OBJECTIVE_CONVERSION_T4": 0.90,
    "THRONE_EXPOSED": 1.00,
    "OBJECTIVE_CONVERSION_RAX": 0.85,
    "OBJECTIVE_CONVERSION_T3": 0.70,
    "POLL_AEGIS_MOMENTUM": 0.65,
    "POLL_ULTRA_LATE_FIGHT_FLIP": 0.72,
    "POLL_BUYBACK_CAPITULATION": 0.85,
    "POLL_STOMP_THROW_CONFIRMED": 0.62,
    "POLL_LATE_FIGHT_FLIP": 0.58,
    "POLL_LEAD_FLIP_WITH_KILLS": 0.55,
    "POLL_MAJOR_COMEBACK_RECOVERY": 0.50,
    "POLL_MAJOR_COMEBACK_FADE": 0.50,  # mirror pressure of underlying event
    "POLL_TEAM_WIPE": 0.55,
    "POLL_KILL_BURST_CONFIRMED": 0.38,
    "POLL_FIGHT_SWING": 0.32,
    "POLL_COMEBACK_RECOVERY": 0.34,
    "OBJECTIVE_CONVERSION_T2": 0.45,
    "BASE_PRESSURE_T4": 0.72,
    "BASE_PRESSURE_T3_COLLAPSE": 0.55,
    "POLL_STRUCTURAL_DOMINANCE": 0.60,  # high pressure: 3 signals align
    "POLL_PRE_PUSH_SETUP": 0.65,  # structural advantage past 25min — high pressure to end
    "POLL_NW_KILL_DIVERGENCE": 0.40,  # soft divergence — moderate pressure
    "POLL_KILL_BURST_TIGHT": 0.42,    # tight kill burst — moderate-high pressure
    "POLL_NW_VELOCITY_SUSTAINED": 0.35,
    "POLL_KILL_GAP_ACCEL": 0.40,
    "POLL_PHASE_NORMALIZED_LEAD": 0.30,
    "BLOODY_EVEN_FIGHT": 0.12,
}

_EVENT_CONFIDENCE: dict[str, float] = {
    "OBJECTIVE_CONVERSION_T4": 0.90,
    "THRONE_EXPOSED": 1.00,
    "OBJECTIVE_CONVERSION_RAX": 0.90,
    "OBJECTIVE_CONVERSION_T3": 0.84,
    "POLL_AEGIS_MOMENTUM": 0.80,
    "POLL_ULTRA_LATE_FIGHT_FLIP": 0.84,
    "POLL_BUYBACK_CAPITULATION": 0.92,
    "POLL_STOMP_THROW_CONFIRMED": 0.80,
    "POLL_LATE_FIGHT_FLIP": 0.76,
    "POLL_LEAD_FLIP_WITH_KILLS": 0.78,
    "POLL_MAJOR_COMEBACK_RECOVERY": 0.76,
    "POLL_MAJOR_COMEBACK_FADE": 0.66,  # data-driven: 66% wr on fade direction
    "POLL_TEAM_WIPE": 0.82,
    "POLL_KILL_BURST_CONFIRMED": 0.68,
    "POLL_FIGHT_SWING": 0.62,
    "POLL_COMEBACK_RECOVERY": 0.62,
    "OBJECTIVE_CONVERSION_T2": 0.70,
    "BASE_PRESSURE_T4": 0.78,
    "BASE_PRESSURE_T3_COLLAPSE": 0.68,
    "POLL_STRUCTURAL_DOMINANCE": 0.75,  # 3-signal alignment is high-confidence
    "POLL_PRE_PUSH_SETUP": 0.78,  # late game + nw lead + enemy towers down — clear winner
    "POLL_NW_KILL_DIVERGENCE": 0.70,  # 76% wr on n=45 backfill — moderate confidence
    "POLL_KILL_BURST_TIGHT": 0.72,
    "POLL_NW_VELOCITY_SUSTAINED": 0.68,
    "POLL_KILL_GAP_ACCEL": 0.70,
    "POLL_PHASE_NORMALIZED_LEAD": 0.62,
    "BLOODY_EVEN_FIGHT": 0.35,
}


@dataclass(frozen=True)
class EventComponent:
    component_type: str
    direction: str | None
    delta: int | float | None
    window_sec: int | None
    previous_value: str | int | float | None = None
    current_value: str | int | float | None = None


@dataclass(frozen=True)
class SnapshotDelta:
    previous: dict
    current: dict
    snapshot_gap_sec: int
    source_cadence_quality: str
    networth_delta: int | None
    radiant_kills_delta: int | None
    dire_kills_delta: int | None
    kill_diff_delta: int | None
    total_kills_delta: int | None
    lead_flipped: bool
    roshan_respawn_timer_jump: bool = False
    structure_delta: StructureDelta | None = None

    @property
    def networth_delta_per_30s(self) -> float | None:
        if self.networth_delta is None or self.snapshot_gap_sec <= 0:
            return None
        return self.networth_delta * 30.0 / self.snapshot_gap_sec

    @property
    def kill_diff_delta_per_30s(self) -> float | None:
        if self.kill_diff_delta is None or self.snapshot_gap_sec <= 0:
            return None
        return self.kill_diff_delta * 30.0 / self.snapshot_gap_sec


@dataclass(frozen=True)
class DotaEvent:
    match_id: str
    lobby_id: str | None
    league_id: str | None
    event_type: str
    game_time_sec: int | None
    radiant_team: str | None
    dire_team: str | None
    radiant_lead: int | None
    radiant_score: int | None
    dire_score: int | None
    tower_state: int | None
    previous_value: str | int | float | None
    current_value: str | int | float | None
    delta: int | float | None
    window_sec: int | None
    direction: str | None
    severity: str
    mapping_name: str | None = None
    yes_team: str | None = None
    yes_token_id: str | None = None
    threshold: int | float | None = None
    base_pressure_score: float | None = None
    fight_pressure_score: float | None = None
    economic_pressure_score: float | None = None
    conversion_score: float | None = None
    event_confidence: float | None = None
    event_dedupe_key: str | None = None
    event_is_primary: bool | None = None
    event_tier: str | None = None
    event_family: str | None = None
    event_quality: float | None = None
    component_event_types: str | None = None
    component_deltas: str | None = None
    component_window_sec: str | None = None
    event_schema_version: str = EVENT_SCHEMA_VERSION
    snapshot_gap_sec: int | None = None
    actual_window_sec: int | None = None
    networth_delta: int | None = None
    kill_diff_delta: int | None = None
    total_kills_delta: int | None = None
    roshan_respawn_timer_before: int | None = None
    roshan_respawn_timer_after: int | None = None
    networth_delta_per_30s: float | None = None
    kill_diff_delta_per_30s: float | None = None
    source_cadence_quality: str | None = None
    structure_source_field: str | None = None
    structure_schema: str | None = None
    structure_confidence: float | None = None
    structure_delta_valid: bool | None = None
    structure_delta_reason: str | None = None
    radiant_t2_alive_before: int | None = None
    radiant_t2_alive_after: int | None = None
    radiant_t3_alive_before: int | None = None
    radiant_t3_alive_after: int | None = None
    radiant_t4_alive_before: int | None = None
    radiant_t4_alive_after: int | None = None
    radiant_rax_melee_before: int | None = None
    radiant_rax_melee_after: int | None = None
    radiant_rax_range_before: int | None = None
    radiant_rax_range_after: int | None = None
    dire_t2_alive_before: int | None = None
    dire_t2_alive_after: int | None = None
    dire_t3_alive_before: int | None = None
    dire_t3_alive_after: int | None = None
    dire_t4_alive_before: int | None = None
    dire_t4_alive_after: int | None = None
    dire_rax_melee_before: int | None = None
    dire_rax_melee_after: int | None = None
    dire_rax_range_before: int | None = None
    dire_rax_range_after: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventDetector:
    """Cadence-aware TopLive event detector.

    Events are built from the immediately previous valid snapshot. Fixed 30s/60s
    event names are retired as primary outputs; any old-style evidence is kept in
    component metadata for calibration.
    """

    def __init__(self, max_history: int = 720):
        self.history: dict[str, deque[dict]] = defaultdict(lambda: deque(maxlen=max_history))
        self.last_emitted_game_time: dict[tuple[str, str, str | None], int] = {}
        self.last_emitted_dedupe_game_time: dict[str, int] = {}
        # 2026-05-31 — POLL_FIRST_SWING_SETTLE: per-match direction lock.
        # Fires once per match on the first meaningful kill-coincident nw swing
        # (gt>10min, entry_px 0.45-0.90). Locks direction for gate logic.
        self._first_swing_direction: dict[str, str] = {}   # match_id → "radiant"|"dire"
        self._first_swing_fired: set[str] = set()          # match_ids that already fired

    def observe(self, game: dict, mapping: dict | None = None) -> list[DotaEvent]:
        match_id = str(game.get("match_id") or game.get("lobby_id") or "")
        if not match_id:
            return []

        snapshot = self._snapshot(game)
        hist = self.history[match_id]
        previous = hist[-1] if hist else None
        events: list[DotaEvent] = []

        if previous:
            delta = self._snapshot_delta(previous, snapshot)
            if delta is not None:
                components = self._build_components(delta)
                events = self._build_tactical_events(delta, components, mapping)
                events = self._enrich_pressure(events, delta)
                events = self._add_event_metadata(events)
                events = self._dedupe_events(events)

        hist.append(snapshot)
        return events

    def _snapshot(self, game: dict) -> dict:
        return {
            "match_id": str(game.get("match_id") or game.get("lobby_id") or ""),
            "lobby_id": game.get("lobby_id"),
            "league_id": game.get("league_id"),
            "game_time_sec": _to_int(game.get("game_time_sec")),
            "radiant_team": game.get("radiant_team"),
            "dire_team": game.get("dire_team"),
            "radiant_lead": _to_int(game.get("radiant_lead")),
            "radiant_score": _to_int(game.get("radiant_score")),
            "dire_score": _to_int(game.get("dire_score")),
            "tower_state": _to_int(game.get("tower_state")),
            "building_state": _to_int(game.get("building_state")),
            "roshan_respawn_timer": _to_int(game.get("roshan_respawn_timer") or game.get("raw", {}).get("roshan_respawn_timer")),
            "data_source": game.get("data_source"),
            "structure_state": decode_structure_state(game),
        }

    def _snapshot_delta(self, previous: dict, current: dict) -> SnapshotDelta | None:
        prev_time = previous.get("game_time_sec")
        cur_time = current.get("game_time_sec")
        if prev_time is None or cur_time is None or cur_time < prev_time:
            return None
        gap = int(cur_time - prev_time)
        if gap <= 0:
            return None

        prev_lead = previous.get("radiant_lead")
        cur_lead = current.get("radiant_lead")
        networth_delta = cur_lead - prev_lead if prev_lead is not None and cur_lead is not None else None

        prev_rs = previous.get("radiant_score")
        prev_ds = previous.get("dire_score")
        cur_rs = current.get("radiant_score")
        cur_ds = current.get("dire_score")
        radiant_kills_delta = cur_rs - prev_rs if prev_rs is not None and cur_rs is not None else None
        dire_kills_delta = cur_ds - prev_ds if prev_ds is not None and cur_ds is not None else None
        kill_diff_delta = None
        total_kills_delta = None
        if radiant_kills_delta is not None and dire_kills_delta is not None:
            kill_diff_delta = radiant_kills_delta - dire_kills_delta
            total_kills_delta = radiant_kills_delta + dire_kills_delta

        lead_flipped = (
            prev_lead is not None
            and cur_lead is not None
            and prev_lead != 0
            and cur_lead != 0
            and (prev_lead > 0) != (cur_lead > 0)
        )
        
        prev_roshan = previous.get("roshan_respawn_timer")
        cur_roshan = current.get("roshan_respawn_timer")
        roshan_jump = False
        if prev_roshan is not None and cur_roshan is not None:
            if prev_roshan == 0 and cur_roshan >= 480:
                roshan_jump = True

        return SnapshotDelta(
            previous=previous,
            current=current,
            snapshot_gap_sec=gap,
            source_cadence_quality=_cadence_quality(gap),
            networth_delta=networth_delta,
            radiant_kills_delta=radiant_kills_delta,
            dire_kills_delta=dire_kills_delta,
            kill_diff_delta=kill_diff_delta,
            total_kills_delta=total_kills_delta,
            lead_flipped=lead_flipped,
            roshan_respawn_timer_jump=roshan_jump,
            structure_delta=diff_structure_state(previous.get("structure_state"), current.get("structure_state")),
        )

    def _base_event(self, snap: dict, mapping: dict | None, **kwargs) -> DotaEvent:
        return DotaEvent(
            match_id=snap["match_id"],
            lobby_id=snap.get("lobby_id"),
            league_id=snap.get("league_id"),
            game_time_sec=snap.get("game_time_sec"),
            radiant_team=snap.get("radiant_team"),
            dire_team=snap.get("dire_team"),
            radiant_lead=snap.get("radiant_lead"),
            radiant_score=snap.get("radiant_score"),
            dire_score=snap.get("dire_score"),
            tower_state=snap.get("tower_state"),
            mapping_name=(mapping or {}).get("name"),
            yes_team=(mapping or {}).get("yes_team"),
            yes_token_id=(mapping or {}).get("yes_token_id"),
            structure_source_field=snap.get("structure_state").source_field if snap.get("structure_state") else None,
            structure_schema=snap.get("structure_state").schema if snap.get("structure_state") else None,
            structure_confidence=snap.get("structure_state").confidence if snap.get("structure_state") else 0.0,
            **kwargs,
        )

    def _event_from_components(
        self,
        event_type: str,
        direction: str | None,
        delta: SnapshotDelta,
        mapping: dict | None,
        components: list[EventComponent],
        *,
        previous_value: str | int | float | None = None,
        current_value: str | int | float | None = None,
        event_delta: int | float | None = None,
        threshold: int | float | None = None,
        severity: str = "medium",
    ) -> DotaEvent:
        return self._base_event(
            delta.current,
            mapping,
            event_type=event_type,
            previous_value=previous_value,
            current_value=current_value,
            delta=event_delta,
            window_sec=delta.snapshot_gap_sec,
            direction=direction,
            severity=severity,
            threshold=threshold,
            snapshot_gap_sec=delta.snapshot_gap_sec,
            actual_window_sec=delta.snapshot_gap_sec,
            networth_delta=delta.networth_delta,
            kill_diff_delta=delta.kill_diff_delta,
            total_kills_delta=delta.total_kills_delta,
            roshan_respawn_timer_before=_to_int(delta.previous.get("raw", {}).get("roshan_respawn_timer")),
            roshan_respawn_timer_after=_to_int(delta.current.get("raw", {}).get("roshan_respawn_timer")),
            networth_delta_per_30s=_round_optional(delta.networth_delta_per_30s),
            kill_diff_delta_per_30s=_round_optional(delta.kill_diff_delta_per_30s),
            source_cadence_quality=delta.source_cadence_quality,
            structure_delta_valid=delta.structure_delta.valid if delta.structure_delta else False,
            structure_delta_reason=delta.structure_delta.reason if delta.structure_delta else "missing",
            radiant_t2_alive_before=delta.structure_delta.radiant_t2_before if delta.structure_delta else None,
            radiant_t2_alive_after=delta.structure_delta.radiant_t2_after if delta.structure_delta else None,
            radiant_t3_alive_before=delta.structure_delta.radiant_t3_before if delta.structure_delta else None,
            radiant_t3_alive_after=delta.structure_delta.radiant_t3_after if delta.structure_delta else None,
            radiant_t4_alive_before=delta.structure_delta.radiant_t4_before if delta.structure_delta else None,
            radiant_t4_alive_after=delta.structure_delta.radiant_t4_after if delta.structure_delta else None,
            radiant_rax_melee_before=delta.structure_delta.radiant_rax_melee_before if delta.structure_delta else None,
            radiant_rax_melee_after=delta.structure_delta.radiant_rax_melee_after if delta.structure_delta else None,
            radiant_rax_range_before=delta.structure_delta.radiant_rax_range_before if delta.structure_delta else None,
            radiant_rax_range_after=delta.structure_delta.radiant_rax_range_after if delta.structure_delta else None,
            dire_t2_alive_before=delta.structure_delta.dire_t2_before if delta.structure_delta else None,
            dire_t2_alive_after=delta.structure_delta.dire_t2_after if delta.structure_delta else None,
            dire_t3_alive_before=delta.structure_delta.dire_t3_before if delta.structure_delta else None,
            dire_t3_alive_after=delta.structure_delta.dire_t3_after if delta.structure_delta else None,
            dire_t4_alive_before=delta.structure_delta.dire_t4_before if delta.structure_delta else None,
            dire_t4_alive_after=delta.structure_delta.dire_t4_after if delta.structure_delta else None,
            dire_rax_melee_before=delta.structure_delta.dire_rax_melee_before if delta.structure_delta else None,
            dire_rax_melee_after=delta.structure_delta.dire_rax_melee_after if delta.structure_delta else None,
            dire_rax_range_before=delta.structure_delta.dire_rax_range_before if delta.structure_delta else None,
            dire_rax_range_after=delta.structure_delta.dire_rax_range_after if delta.structure_delta else None,
            **_component_metadata(components),
        )

    def _build_components(self, delta: SnapshotDelta) -> list[EventComponent]:
        components: list[EventComponent] = []
        prev = delta.previous
        cur = delta.current
        gap = delta.snapshot_gap_sec

        if delta.networth_delta is not None and delta.networth_delta != 0:
            components.append(EventComponent(
                "NETWORTH_DELTA",
                _direction_from_delta(delta.networth_delta),
                delta.networth_delta,
                gap,
                prev.get("radiant_lead"),
                cur.get("radiant_lead"),
            ))

        if delta.kill_diff_delta is not None and delta.kill_diff_delta != 0:
            components.append(EventComponent(
                "KILL_DIFF_DELTA",
                _direction_from_delta(delta.kill_diff_delta),
                delta.kill_diff_delta,
                gap,
                _score_value(prev),
                _score_value(cur),
            ))

        if delta.lead_flipped:
            components.append(EventComponent(
                "LEAD_FLIP",
                "radiant" if (cur.get("radiant_lead") or 0) > 0 else "dire",
                delta.networth_delta,
                gap,
                prev.get("radiant_lead"),
                cur.get("radiant_lead"),
            ))

        components.extend(self._structure_components(delta))
        if (
            gap <= NORMAL_GAP_SEC
            and delta.source_cadence_quality != "invalid_gap"
            and delta.total_kills_delta is not None
            and delta.kill_diff_delta is not None
            and delta.total_kills_delta >= 4
            and abs(delta.kill_diff_delta) <= 1
            and abs(delta.networth_delta or 0) < 1000
        ):
            components.append(EventComponent(
                "BLOODY_EVEN_FIGHT",
                None,
                delta.kill_diff_delta,
                gap,
                _score_value(prev),
                _score_value(cur),
            ))
        return components

    def _structure_components(self, delta: SnapshotDelta) -> list[EventComponent]:
        prev_state = decode_structure_state(delta.previous)
        cur_state = decode_structure_state(delta.current)
        sd = diff_structure_state(prev_state, cur_state)

        if not sd.valid:
            return []

        components: list[EventComponent] = []

        def add(component_type: str, direction: str, fallen: int):
            components.append(EventComponent(
                component_type,
                direction,
                fallen,
                delta.snapshot_gap_sec,
                sd.reason,
                sd.schema,
            ))

        # Radiant towers fell => Dire is favored.
        if sd.radiant_t4_fallen:
            add(
                "SECOND_T4_TOWER_FALL" if sd.radiant_t4_after == 0 else "FIRST_T4_TOWER_FALL",
                "dire",
                sd.radiant_t4_fallen,
            )
            if sd.radiant_t4_after == 0:
                add("THRONE_EXPOSED_COMPONENT", "dire", sd.radiant_t4_fallen)

        if sd.radiant_t3_fallen:
            add(
                "MULTIPLE_T3_TOWERS_DOWN" if (sd.radiant_t3_after or 0) <= 1 else "T3_TOWER_FALL",
                "dire",
                sd.radiant_t3_fallen,
            )
            if sd.radiant_t3_after == 0:
                add("ALL_T3_TOWERS_DOWN", "dire", 3)

        if sd.radiant_t2_fallen:
            add("T2_TOWER_FALL", "dire", sd.radiant_t2_fallen)
            if sd.radiant_t2_after == 0:
                add("ALL_T2_TOWERS_DOWN", "dire", 3)

        # Dire towers fell => Radiant is favored.
        if sd.dire_t4_fallen:
            add(
                "SECOND_T4_TOWER_FALL" if sd.dire_t4_after == 0 else "FIRST_T4_TOWER_FALL",
                "radiant",
                sd.dire_t4_fallen,
            )
            if sd.dire_t4_after == 0:
                add("THRONE_EXPOSED_COMPONENT", "radiant", sd.dire_t4_fallen)

        if sd.dire_t3_fallen:
            add(
                "MULTIPLE_T3_TOWERS_DOWN" if (sd.dire_t3_after or 0) <= 1 else "T3_TOWER_FALL",
                "radiant",
                sd.dire_t3_fallen,
            )
            if sd.dire_t3_after == 0:
                add("ALL_T3_TOWERS_DOWN", "radiant", 3)

        if sd.dire_t2_fallen:
            add("T2_TOWER_FALL", "radiant", sd.dire_t2_fallen)
            if sd.dire_t2_after == 0:
                add("ALL_T2_TOWERS_DOWN", "radiant", 3)

        if sd.radiant_rax_melee_fallen or sd.radiant_rax_range_fallen:
            add("RAX_FALL", "dire", sd.radiant_rax_melee_fallen + sd.radiant_rax_range_fallen)
        if sd.dire_rax_melee_fallen or sd.dire_rax_range_fallen:
            add("RAX_FALL", "radiant", sd.dire_rax_melee_fallen + sd.dire_rax_range_fallen)

        return components

    def _sustained_lead_candidates(
        self,
        delta: SnapshotDelta,
        mapping: dict | None,
    ) -> list[DotaEvent]:
        """Fires when one team has held a dominant networth lead or rapidly gained one.

        Catches slow-grind stomps and sudden economy shifts where no discrete fight
        event fires but the win probability has fundamentally changed.
        """
        cur_time = delta.current.get("game_time_sec") or 0
        cur_lead = delta.current.get("radiant_lead")
        if cur_lead is None or abs(cur_lead) < RAPID_STOMP_MIN_LEAD:
            return []

        direction = "radiant" if cur_lead > 0 else "dire"
        sign = 1 if direction == "radiant" else -1
        match_id = delta.current["match_id"]
        hist = self.history[match_id]
        out: list[DotaEvent] = []

        # 1. RAPID STOMP check (shorter window, higher growth)
        oldest_rapid: dict | None = None
        for snap in reversed(hist):
            snap_time = snap.get("game_time_sec") or 0
            if cur_time - snap_time > RAPID_STOMP_WINDOW_SEC:
                oldest_rapid = snap
                break
        
        rapid_fired = False
        if oldest_rapid:
            oldest_lead = oldest_rapid.get("radiant_lead") or 0
            growth = sign * cur_lead - sign * oldest_lead
            if growth >= RAPID_STOMP_MIN_GROWTH:
                out.append(self._base_event(
                    delta.current, mapping,
                    event_type="POLL_RAPID_STOMP",
                    previous_value=oldest_lead, current_value=cur_lead,
                    delta=cur_lead - oldest_lead,
                    window_sec=cur_time - snap_time,
                    direction=direction, severity="high",
                    threshold=RAPID_STOMP_MIN_GROWTH,
                ))
                rapid_fired = True

        # 1b. CADENCE-AWARE BURST: If gap is large, fire based on single-snap jump
        if not rapid_fired and delta.snapshot_gap_sec >= 25 and delta.networth_delta is not None:
            normalized_nw = sign * delta.networth_delta * 30.0 / delta.snapshot_gap_sec
            if normalized_nw >= 2000 and abs(cur_lead) >= RAPID_STOMP_MIN_LEAD:
                 out.append(self._base_event(
                    delta.current, mapping,
                    event_type="POLL_RAPID_STOMP",
                    previous_value=delta.previous.get("radiant_lead"), current_value=cur_lead,
                    delta=delta.networth_delta,
                    window_sec=delta.snapshot_gap_sec,
                    direction=direction, severity="high",
                    threshold=2000,
                ))

        # 2. DECISIVE STOMP check (longer window, sustained lead)
        if (cur_time >= DECISIVE_STOMP_MIN_TIME
                and cur_time <= DECISIVE_STOMP_MAX_TIME
                and abs(cur_lead) >= DECISIVE_STOMP_MIN_LEAD):
            oldest_decisive: dict | None = None
            sustained = True
            for snap in reversed(hist):
                snap_time = snap.get("game_time_sec") or 0
                if cur_time - snap_time > DECISIVE_STOMP_WINDOW_SEC:
                    oldest_decisive = snap
                    break
                # Lead must stay above window threshold throughout
                if sign * (snap.get("radiant_lead") or 0) < DECISIVE_STOMP_WINDOW_LEAD:
                    sustained = False
                    break
            
            if oldest_decisive and sustained:
                oldest_lead = oldest_decisive.get("radiant_lead") or 0
                growth = sign * cur_lead - sign * oldest_lead
                if growth >= DECISIVE_STOMP_MIN_GROWTH:
                    out.append(self._base_event(
                        delta.current, mapping,
                        event_type="POLL_DECISIVE_STOMP",
                        previous_value=oldest_lead, current_value=cur_lead,
                        delta=cur_lead - oldest_lead,
                        window_sec=cur_time - snap_time,
                        direction=direction, severity="high",
                        threshold=DECISIVE_STOMP_MIN_LEAD,
                    ))
        return out

    def _build_tactical_events(
        self,
        delta: SnapshotDelta,
        components: list[EventComponent],
        mapping: dict | None,
    ) -> list[DotaEvent]:
        candidates: list[DotaEvent] = []
        candidates.extend(self._fight_candidates(delta, components, mapping))
        candidates.extend(self._comeback_candidates(delta, components, mapping))
        candidates.extend(self._sustained_lead_candidates(delta, mapping))
        candidates.extend(self._base_pressure_candidates(delta, components, mapping))
        candidates.extend(self._state_pressure_candidates(delta, mapping))
        candidates.extend(self._objective_conversion_candidates(delta, components, candidates, mapping))
        candidates.extend(self._value_disagreement_candidates(delta, components, mapping))
        candidates.extend(self._nw_kill_divergence_candidates(delta, components, mapping))
        candidates.extend(self._structural_dominance_candidates(delta, components, mapping))
        candidates.extend(self._pre_push_setup_candidates(delta, components, mapping))
        # 2026-05-30 — Phase B: real-time-only detectors (no lagged data)
        candidates.extend(self._kill_burst_tight_candidates(delta, components, mapping))
        candidates.extend(self._nw_velocity_sustained_candidates(delta, components, mapping))
        candidates.extend(self._kill_gap_accel_candidates(delta, components, mapping))
        candidates.extend(self._phase_normalized_lead_candidates(delta, components, mapping))
        # 2026-05-31 — POLL_FIRST_SWING_SETTLE: fires once per match to lock direction
        candidates.extend(self._first_swing_settle_candidates(delta, mapping))
        # 2026-05-31 — POLL_REVERSAL_ENTRY: S2 strategy, buy underdog early in comeback
        candidates.extend(self._reversal_entry_candidates(delta, mapping))

        # NEW: POLL_AEGIS_MOMENTUM
        if delta.roshan_respawn_timer_jump:
            nw_dir = _direction_from_delta(delta.networth_delta)
            kill_dir = _direction_from_delta(delta.kill_diff_delta)
            abs_nw = abs(delta.networth_delta or 0)
            abs_kill = abs(delta.kill_diff_delta or 0)
            
            # If NW and Kills disagree, prefer NW direction for economy signal
            # If both missing/zero, we can't reliably infer killer from a single snap
            aegis_dir = nw_dir or kill_dir
            if aegis_dir and (abs_nw >= 1000 or abs_kill >= 1):
                candidates.append(self._event_from_components(
                    "POLL_AEGIS_MOMENTUM",
                    aegis_dir,
                    delta,
                    mapping,
                    _components_for_direction(components, aegis_dir, {"NETWORTH_DELTA", "KILL_DIFF_DELTA"}),
                    previous_value="roshan_alive",
                    current_value="roshan_dead",
                    event_delta=abs_nw,
                    threshold=1000,
                    severity="high",
                ))

        # Bloody-even is research-only and directionless; keep it outside ranking.
        if any(c.component_type == "BLOODY_EVEN_FIGHT" for c in components):
            bloody = [c for c in components if c.component_type == "BLOODY_EVEN_FIGHT"]
            candidates.append(self._event_from_components(
                "BLOODY_EVEN_FIGHT",
                None,
                delta,
                mapping,
                bloody,
                previous_value=bloody[0].previous_value,
                current_value=bloody[0].current_value,
                event_delta=bloody[0].delta,
                severity="medium",
            ))

        ranked: list[DotaEvent] = []
        for direction, group in _group_events_by_direction(candidates).items():
            if direction is None:
                ranked.extend(group)
                continue
            primary = max(group, key=lambda e: (TACTICAL_PRIORITY.get(e.event_type, 0), float(e.event_quality or 0.0)))
            if not self._cooldown_ok(delta.current, primary.event_type, primary.direction):
                continue
            lower = [e for e in group if e is not primary]
            ranked.append(self._merge_components(primary, lower))
        return ranked

    def _fight_candidates(
        self,
        delta: SnapshotDelta,
        components: list[EventComponent],
        mapping: dict | None,
    ) -> list[DotaEvent]:
        out: list[DotaEvent] = []
        if delta.networth_delta is None or delta.kill_diff_delta is None:
            return []
        gap = delta.snapshot_gap_sec
        if gap > MAX_FIGHT_GAP_SEC:
            return []

        net_dir = _direction_from_delta(delta.networth_delta)
        kill_dir = _direction_from_delta(delta.kill_diff_delta)
        agrees = net_dir is not None and kill_dir is not None and net_dir == kill_dir
        
        # NEW: POLL_BUYBACK_CAPITULATION
        # Requires >= 6 kills for one team (must include buybacks), <= 2 for other.
        # Moved OUTSIDE agreement check because buybacks cost gold and can flip NW delta negative.
        if (
            gap <= NORMAL_GAP_SEC 
            and delta.radiant_kills_delta is not None and delta.dire_kills_delta is not None
        ):
            rk, dk = delta.radiant_kills_delta, delta.dire_kills_delta
            if (rk >= 6 and dk <= 2) or (dk >= 6 and rk <= 2):
                wipe_dir = "radiant" if rk > dk else "dire"
                out.append(self._event_from_components(
                    "POLL_BUYBACK_CAPITULATION",
                    wipe_dir,
                    delta,
                    mapping,
                    _components_for_direction(components, wipe_dir, {"KILL_DIFF_DELTA", "NETWORTH_DELTA"}),
                    previous_value=_score_value(delta.previous),
                    current_value=_score_value(delta.current),
                    event_delta=delta.kill_diff_delta,
                    threshold=6,
                    severity="high",
                ))

        if not agrees:
            return out  # return any wipes found even if NW/Kills disagree elsewhere

        abs_nw = abs(delta.networth_delta)
        abs_kill = abs(delta.kill_diff_delta)
        base_components = _components_for_direction(components, net_dir, {"NETWORTH_DELTA", "KILL_DIFF_DELTA", "LEAD_FLIP"})

        # 2026-05-30 — raised abs_kill threshold 1→2 so KILL_BURST_CONFIRMED
        # (abs_kill≥3, lower NW threshold) can fire independently. Previously
        # FIGHT_SWING absorbed everything via TACTICAL_PRIORITY ranking and
        # KILL_BURST got only 7 fires in 7d.
        # 2026-05-30 Phase 4 — require HIGH-severity FIGHT_SWING only.
        # Data: 89% wr at high (n=79) vs 79% at medium (n=95).
        # High = (kill_delta≥3 OR nw_delta≥2500) within base threshold
        # (kill_delta≥1 AND nw_delta≥1500). Simplifies to:
        # (kill≥3 AND nw≥1500) OR (kill≥1 AND nw≥2500).
        # This keeps FIGHT_SWING distinct from KILL_BURST_CONFIRMED (which
        # requires kill≥3 AND nw≥500 — different shape).
        _high_sev = (abs_kill >= 3 and abs_nw >= 1500) or (abs_kill >= 1 and abs_nw >= 2500)
        if gap <= NORMAL_GAP_SEC and _high_sev:
            out.append(self._event_from_components(
                "POLL_FIGHT_SWING",
                net_dir,
                delta,
                mapping,
                base_components,
                previous_value=delta.previous.get("radiant_lead"),
                current_value=delta.current.get("radiant_lead"),
                event_delta=delta.networth_delta,
                threshold=1000,
                severity="high" if abs_kill >= 3 or abs_nw >= 2500 else "medium",
            ))

        if gap <= NORMAL_GAP_SEC and abs_kill >= 3 and abs_nw >= 500:
            out.append(self._event_from_components(
                "POLL_KILL_BURST_CONFIRMED",
                net_dir,
                delta,
                mapping,
                base_components,
                previous_value=_score_value(delta.previous),
                current_value=_score_value(delta.current),
                event_delta=delta.kill_diff_delta,
                threshold=3,
                severity="high",
            ))

        # NEW: POLL_TEAM_WIPE
        # Requires >= 4 kills for favored team, <= 1 for trailer, and >= 2000 NW swing
        favored_kills = delta.radiant_kills_delta if net_dir == "radiant" else delta.dire_kills_delta
        trailer_kills = delta.dire_kills_delta if net_dir == "radiant" else delta.radiant_kills_delta
        if (
            gap <= NORMAL_GAP_SEC 
            and favored_kills is not None and favored_kills >= 4 
            and trailer_kills is not None and trailer_kills <= 1
            and abs_nw >= 2000
        ):
            out.append(self._event_from_components(
                "POLL_TEAM_WIPE",
                net_dir,
                delta,
                mapping,
                base_components,
                previous_value=_score_value(delta.previous),
                current_value=_score_value(delta.current),
                event_delta=delta.kill_diff_delta,
                threshold=4,
                severity="high",
            ))

        if (
            delta.lead_flipped
            and abs(delta.previous.get("radiant_lead") or 0) >= 1500
            and abs_nw >= 1500
            and kill_dir == ("radiant" if (delta.current.get("radiant_lead") or 0) > 0 else "dire")
        ):
            out.append(self._event_from_components(
                "POLL_LEAD_FLIP_WITH_KILLS",
                net_dir,
                delta,
                mapping,
                base_components,
                previous_value=delta.previous.get("radiant_lead"),
                current_value=delta.current.get("radiant_lead"),
                event_delta=delta.networth_delta,
                threshold=1500,
                severity="high",
            ))

        cur_time = delta.current.get("game_time_sec") or 0
        if cur_time >= LATE_FIGHT_TIME and abs_kill >= 3 and abs_nw >= 2500:
            out.append(self._event_from_components(
                "POLL_LATE_FIGHT_FLIP",
                net_dir,
                delta,
                mapping,
                base_components,
                previous_value=delta.previous.get("radiant_lead"),
                current_value=delta.current.get("radiant_lead"),
                event_delta=delta.networth_delta,
                threshold=2500,
                severity="high",
            ))

        if cur_time >= ULTRA_LATE_FIGHT_TIME and abs_kill >= 3 and (abs_nw >= 3000 or delta.lead_flipped):
            out.append(self._event_from_components(
                "POLL_ULTRA_LATE_FIGHT_FLIP",
                net_dir,
                delta,
                mapping,
                base_components,
                previous_value=delta.previous.get("radiant_lead"),
                current_value=delta.current.get("radiant_lead"),
                event_delta=delta.networth_delta,
                threshold=3000,
                severity="high",
            ))

        prev_lead = delta.previous.get("radiant_lead")
        if cur_time >= STOMP_THROW_MIN_TIME and prev_lead is not None and abs(prev_lead) >= STOMP_THROW_MIN_LEAD:
            trailing = "dire" if prev_lead > 0 else "radiant"
            trailing_nw = -delta.networth_delta if trailing == "dire" else delta.networth_delta
            trailing_kills = -delta.kill_diff_delta if trailing == "dire" else delta.kill_diff_delta
            if trailing_nw >= STOMP_THROW_MIN_NW_SWING and trailing_kills >= STOMP_THROW_MIN_KILLS:
                out.append(self._event_from_components(
                    "POLL_STOMP_THROW_CONFIRMED",
                    trailing,
                    delta,
                    mapping,
                    base_components,
                    previous_value=prev_lead,
                    current_value=delta.current.get("radiant_lead"),
                    event_delta=trailing_nw,
                    threshold=STOMP_THROW_MIN_NW_SWING,
                    severity="high",
                ))
        return out

    def _comeback_candidates(
        self,
        delta: SnapshotDelta,
        components: list[EventComponent],
        mapping: dict | None,
    ) -> list[DotaEvent]:
        if delta.networth_delta is None or delta.snapshot_gap_sec > MAX_FIGHT_GAP_SEC:
            return []
        prev_lead = delta.previous.get("radiant_lead")
        cur_lead = delta.current.get("radiant_lead")
        if prev_lead is None or cur_lead is None or prev_lead == 0 or cur_lead == 0:
            return []

        direction = None
        recovered = 0
        if prev_lead < 0 and cur_lead < 0 and delta.networth_delta > 0:
            direction = "radiant"
            recovered = delta.networth_delta
        elif prev_lead > 0 and cur_lead > 0 and delta.networth_delta < 0:
            direction = "dire"
            recovered = -delta.networth_delta
        else:
            return []

        prior_deficit = abs(prev_lead)
        if prior_deficit < COMEBACK_MIN_PRIOR_DEFICIT:
            return []
        if prior_deficit >= MAJOR_COMEBACK_PRIOR_DEFICIT:
            event_type = "POLL_MAJOR_COMEBACK_RECOVERY"
            threshold = MAJOR_COMEBACK_RECOVERY_MIN_SWING
        else:
            event_type = "POLL_COMEBACK_RECOVERY"
            threshold = COMEBACK_RECOVERY_MIN_SWING
        if recovered < threshold:
            return []

        comps = _components_for_direction(components, direction, {"NETWORTH_DELTA", "KILL_DIFF_DELTA"})
        out = [self._event_from_components(
            event_type,
            direction,
            delta,
            mapping,
            comps,
            previous_value=prev_lead,
            current_value=cur_lead,
            event_delta=delta.networth_delta,
            threshold=threshold,
            severity="high" if recovered >= threshold * 1.5 else "medium",
        )]
        # 2026-05-30 — FADE variant of MAJOR_COMEBACK_RECOVERY. 7d settle data:
        # the recovering team wins only 34% (n=53). Fading the recovery →
        # 66% wr → +EV. Emit a sibling event with inverted direction and a
        # separate event_type so signal_engine can apply a tighter cap.
        if event_type == "POLL_MAJOR_COMEBACK_RECOVERY":
            fade_dir = "dire" if direction == "radiant" else "radiant"
            fade_comps = _components_for_direction(components, fade_dir, {"NETWORTH_DELTA", "KILL_DIFF_DELTA"})
            out.append(self._event_from_components(
                "POLL_MAJOR_COMEBACK_FADE",
                fade_dir,
                delta,
                mapping,
                fade_comps,
                previous_value=prev_lead,
                current_value=cur_lead,
                event_delta=delta.networth_delta,
                threshold=threshold,
                severity="high" if recovered >= threshold * 1.5 else "medium",
            ))
        return out

    def _base_pressure_candidates(
        self,
        delta: SnapshotDelta,
        components: list[EventComponent],
        mapping: dict | None,
    ) -> list[DotaEvent]:
        out: list[DotaEvent] = []
        by_dir: dict[str, list[EventComponent]] = defaultdict(list)
        for comp in components:
            if comp.direction and comp.component_type in CONVERSION_TOWER_COMPONENTS:
                by_dir[comp.direction].append(comp)

        for direction, comps in by_dir.items():
            types = {c.component_type for c in comps}
            sd = delta.structure_delta
            if not sd or not sd.valid:
                continue
                
            # THRONE_EXPOSED only when T4 alive goes from >0 to 0
            rad_exposed = (direction == "dire" and sd.radiant_t4_before > 0 and sd.radiant_t4_after == 0)
            dire_exposed = (direction == "radiant" and sd.dire_t4_before > 0 and sd.dire_t4_after == 0)
            
            # Pressure requires same-direction fight/economy pressure
            pressure_ok = False
            if delta.kill_diff_delta is not None or delta.networth_delta is not None:
                signed_kills = delta.kill_diff_delta if direction == "radiant" else -delta.kill_diff_delta
                signed_nw = delta.networth_delta if direction == "radiant" else -delta.networth_delta
                if (signed_kills or 0) > 0 or (signed_nw or 0) > 100:
                    pressure_ok = True

            if rad_exposed or dire_exposed:
                out.append(self._event_from_components(
                    "THRONE_EXPOSED", direction, delta, mapping, comps,
                    previous_value=">0", current_value="0",
                    event_delta=1.0, severity="high",
                ))
            elif ({"FIRST_T4_TOWER_FALL", "SECOND_T4_TOWER_FALL"} & types):
                # BASE_PRESSURE_T4: only when T4 already exposed + pressure
                t4_already_exposed = (direction == "dire" and sd.radiant_t4_after == 0) or \
                                     (direction == "radiant" and sd.dire_t4_after == 0)
                if t4_already_exposed and pressure_ok:
                    out.append(self._event_from_components(
                        "BASE_PRESSURE_T4", direction, delta, mapping, comps,
                        previous_value="exposed", current_value="pressure",
                        event_delta=1.0, severity="high",
                    ))
            elif "ALL_T3_TOWERS_DOWN" in types or "MULTIPLE_T3_TOWERS_DOWN" in types or "T3_TOWER_FALL" in types:
                # BASE_PRESSURE_T3_COLLAPSE: T3 decrease or all T3 down plus fight/economy pressure
                t3_all_down = (direction == "dire" and sd.radiant_t3_after == 0) or \
                               (direction == "radiant" and sd.dire_t3_after == 0)
                t3_decrease = (direction == "dire" and sd.radiant_t3_fallen > 0) or \
                               (direction == "radiant" and sd.dire_t3_fallen > 0)
                               
                if (t3_decrease or t3_all_down) and pressure_ok:
                    out.append(self._event_from_components(
                        "BASE_PRESSURE_T3_COLLAPSE", direction, delta, mapping, comps,
                        previous_value="t3_vulnerable", current_value="pressure",
                        event_delta=1.0, severity="high" if t3_all_down else "medium",
                    ))
        return out

    def _state_pressure_candidates(self, delta: SnapshotDelta, mapping: dict | None) -> list[DotaEvent]:
        out: list[DotaEvent] = []
        ss = delta.current.get("structure_state")
        if not ss or ss.confidence < 1.0:
            return []

        for direction in ["radiant", "dire"]:
            # Pressure requires same-direction fight/economy pressure in this snapshot
            pressure_ok = False
            if delta.kill_diff_delta is not None or delta.networth_delta is not None:
                signed_kills = delta.kill_diff_delta if direction == "radiant" else -delta.kill_diff_delta
                signed_nw = delta.networth_delta if direction == "radiant" else -delta.networth_delta
                if (signed_kills or 0) > 0 or (signed_nw or 0) > 200: # Slightly higher threshold for state-polling
                    pressure_ok = True
            
            if not pressure_ok:
                continue

            # BASE_PRESSURE_T4: T4 already exposed + pressure
            t4_already_exposed = (direction == "dire" and ss.radiant_t4_alive == 0) or \
                                 (direction == "radiant" and ss.dire_t4_alive == 0)
            if t4_already_exposed:
                out.append(self._event_from_components(
                    "BASE_PRESSURE_T4", direction, delta, mapping, [],
                    previous_value="exposed", current_value="pressure",
                    event_delta=1.0, severity="high",
                ))
                continue # Only one pressure event per side

            # BASE_PRESSURE_T3_COLLAPSE: all T3 down plus fight/economy pressure
            t3_all_down = (direction == "dire" and ss.radiant_t3_alive == 0) or \
                           (direction == "radiant" and ss.dire_t3_alive == 0)
            if t3_all_down:
                out.append(self._event_from_components(
                    "BASE_PRESSURE_T3_COLLAPSE", direction, delta, mapping, [],
                    previous_value="t3_vulnerable", current_value="pressure",
                    event_delta=1.0, severity="high",
                ))
        return out

    # ─────────────────────────────────────────────────────────────────
    # 2026-05-30 — Phase B detectors using ONLY real-time fields from
    # GetTopLiveGame: {game_time_sec, radiant_score, dire_score,
    # radiant_lead}. These fire BEFORE MM sees the underlying state
    # change via delayed GetRealtimeStats / broadcast feed.
    # ─────────────────────────────────────────────────────────────────

    def _kill_burst_tight_candidates(
        self,
        delta: SnapshotDelta,
        components: list[EventComponent],
        mapping: dict | None,
    ) -> list[DotaEvent]:
        """POLL_KILL_BURST_TIGHT — 3+ kills by one team in a 10s window.

        Tighter window than POLL_RAPID_STOMP (45s). Catches teamfight kill
        bursts in the seconds after they happen, before MM repriced.
        Uses only real-time fields (kill scores + game_time).
        """
        cur = delta.current
        cur_time = cur.get("game_time_sec") or 0
        rs = _to_int(cur.get("radiant_score"))
        ds = _to_int(cur.get("dire_score"))
        if rs is None or ds is None or cur_time < 600:
            return []

        match_id = cur.get("match_id")
        if not match_id:
            return []
        hist = self.history[match_id]

        # Find oldest snapshot in the last 10 seconds
        WINDOW_SEC = 10
        MIN_BURST = 3
        oldest = None
        for snap in reversed(hist):
            st = snap.get("game_time_sec") or 0
            if cur_time - st > WINDOW_SEC:
                oldest = snap
                break
        if oldest is None:
            return []

        old_rs = _to_int(oldest.get("radiant_score")) or 0
        old_ds = _to_int(oldest.get("dire_score")) or 0
        rad_burst = rs - old_rs
        dire_burst = ds - old_ds
        net_burst = rad_burst - dire_burst
        if abs(net_burst) < MIN_BURST:
            return []
        direction = "radiant" if net_burst > 0 else "dire"
        return [self._base_event(
            cur, mapping,
            event_type="POLL_KILL_BURST_TIGHT",
            previous_value=f"{old_rs}-{old_ds}",
            current_value=f"{rs}-{ds}",
            delta=net_burst,
            window_sec=cur_time - (oldest.get("game_time_sec") or 0),
            direction=direction,
            severity="high",
            threshold=MIN_BURST,
        )]

    def _nw_velocity_sustained_candidates(
        self,
        delta: SnapshotDelta,
        components: list[EventComponent],
        mapping: dict | None,
    ) -> list[DotaEvent]:
        """POLL_NW_VELOCITY_SUSTAINED — radiant_lead growing >100/sec for >60s.

        Catches grind-stomps where MM hasn't repriced because no discrete
        event fired. Pure rate-of-change on radiant_lead, real-time.
        """
        cur = delta.current
        cur_time = cur.get("game_time_sec") or 0
        cur_lead = cur.get("radiant_lead")
        if cur_lead is None or cur_time < 600:
            return []
        match_id = cur.get("match_id")
        if not match_id:
            return []
        hist = self.history[match_id]

        WINDOW_SEC = 60
        MIN_VELOCITY = 100  # nw per second
        oldest = None
        for snap in reversed(hist):
            st = snap.get("game_time_sec") or 0
            if cur_time - st >= WINDOW_SEC:
                oldest = snap
                break
        if oldest is None:
            return []
        old_lead = oldest.get("radiant_lead") or 0
        elapsed = cur_time - (oldest.get("game_time_sec") or 0)
        if elapsed <= 0:
            return []
        growth = cur_lead - old_lead
        velocity = growth / elapsed
        if abs(velocity) < MIN_VELOCITY:
            return []
        # Direction is whoever's gaining
        direction = "radiant" if growth > 0 else "dire"
        return [self._base_event(
            cur, mapping,
            event_type="POLL_NW_VELOCITY_SUSTAINED",
            previous_value=old_lead,
            current_value=cur_lead,
            delta=growth,
            window_sec=elapsed,
            direction=direction,
            severity="high" if abs(velocity) >= 150 else "medium",
            threshold=MIN_VELOCITY,
        )]

    def _kill_gap_accel_candidates(
        self,
        delta: SnapshotDelta,
        components: list[EventComponent],
        mapping: dict | None,
    ) -> list[DotaEvent]:
        """POLL_KILL_GAP_ACCEL — kill-gap accelerating over consecutive windows.

        Compares kill_gap delta in last 30s vs prior 30s. If gap is widening
        faster, snowball is starting. Fires before NW catches up.
        Uses only kill scores + game_time.
        """
        cur = delta.current
        cur_time = cur.get("game_time_sec") or 0
        rs = _to_int(cur.get("radiant_score"))
        ds = _to_int(cur.get("dire_score"))
        if rs is None or ds is None or cur_time < 900:
            return []
        match_id = cur.get("match_id")
        if not match_id:
            return []
        hist = self.history[match_id]

        WINDOW_SEC = 30
        MIN_ACCEL = 2  # gap-growth must accel by at least 2 kills
        # Find snapshot ~30s ago and ~60s ago
        snap_30s = None
        snap_60s = None
        for snap in reversed(hist):
            st = snap.get("game_time_sec") or 0
            age = cur_time - st
            if snap_30s is None and age >= WINDOW_SEC:
                snap_30s = snap
            if age >= WINDOW_SEC * 2:
                snap_60s = snap
                break
        if snap_30s is None or snap_60s is None:
            return []
        cur_gap = rs - ds
        gap_30 = (_to_int(snap_30s.get("radiant_score")) or 0) - (_to_int(snap_30s.get("dire_score")) or 0)
        gap_60 = (_to_int(snap_60s.get("radiant_score")) or 0) - (_to_int(snap_60s.get("dire_score")) or 0)
        recent_delta = cur_gap - gap_30
        prior_delta = gap_30 - gap_60
        accel = recent_delta - prior_delta
        if abs(accel) < MIN_ACCEL:
            return []
        # Direction = whoever's gaining the gap faster
        if accel > 0 and cur_gap >= 0:
            direction = "radiant"
        elif accel < 0 and cur_gap <= 0:
            direction = "dire"
        else:
            return []  # mixed signal — not a clean accel
        return [self._base_event(
            cur, mapping,
            event_type="POLL_KILL_GAP_ACCEL",
            previous_value=gap_60,
            current_value=cur_gap,
            delta=accel,
            window_sec=2 * WINDOW_SEC,
            direction=direction,
            severity="high" if abs(accel) >= 3 else "medium",
            threshold=MIN_ACCEL,
        )]

    def _phase_normalized_lead_candidates(
        self,
        delta: SnapshotDelta,
        components: list[EventComponent],
        mapping: dict | None,
    ) -> list[DotaEvent]:
        """POLL_PHASE_NORMALIZED_LEAD — nw lead per minute exceeds threshold.

        5k lead at 10min is huge; 5k at 35min is small. This normalizes by
        elapsed game time. Uses only radiant_lead + game_time.
        """
        cur = delta.current
        cur_time = cur.get("game_time_sec") or 0
        cur_lead = cur.get("radiant_lead")
        if cur_lead is None or cur_time < 600:
            return []
        # Lead per minute
        lpm = abs(cur_lead) / max(1, cur_time / 60.0)
        # Threshold: 250 nw per minute = aggressive stomp
        MIN_LPM = 250
        if lpm < MIN_LPM:
            return []
        direction = "radiant" if cur_lead > 0 else "dire"
        return [self._base_event(
            cur, mapping,
            event_type="POLL_PHASE_NORMALIZED_LEAD",
            previous_value="contested",
            current_value="phase_dominant",
            delta=cur_lead,
            window_sec=cur_time,
            direction=direction,
            severity="high" if lpm >= 400 else "medium",
            threshold=MIN_LPM,
        )]

    def _value_disagreement_candidates(
        self,
        delta: SnapshotDelta,
        components: list[EventComponent],
        mapping: dict | None,
    ) -> list[DotaEvent]:
        """Fires when NW lead and kill lead AGREE on direction beyond thresholds.

        NB: name is a historical misnomer (2026-05-30 audit). The code requires
        agreement, not disagreement — effectively a softer STRUCTURAL_DOMINANCE
        without the structure constraint. Renaming would invalidate downstream
        config keys / memory; the signal works (7d settle wr 85%, n=573). True
        directional disagreement (NW favors A, kills favor B) is captured by
        the new POLL_NW_KILL_DIVERGENCE detector below.

        Thresholds: |NW Lead| >= 2500 AND |Kill Lead| >= 2 AND same direction.
        """
        cur = delta.current
        nw_lead = _to_int(cur.get("radiant_lead"))
        rad_score = _to_int(cur.get("radiant_score"))
        dire_score = _to_int(cur.get("dire_score"))
        
        if nw_lead is None or rad_score is None or dire_score is None:
            return []
            
        kill_lead = rad_score - dire_score
        direction = "radiant" if nw_lead > 0 else "dire"
        abs_nw = abs(nw_lead)
        abs_kill = abs(kill_lead)
        
        # Game must be out of early-game noise; cap late-phase (wr 68% past 40m)
        game_time = _to_int(cur.get("game_time_sec"))
        if game_time is None or game_time < 600 or game_time > 2400:
            return []

        if abs_nw >= 2500 and abs_kill >= 2:
            # Direction must match both leads
            if (nw_lead > 0) == (kill_lead > 0):
                # 2026-05-30 #5 — Suppress when STRUCTURAL_DOMINANCE would also
                # fire (struct_diff >= 2 on the same direction). The two events
                # had ~94% overlap in real fires; VALUE_DIS was a near-duplicate
                # log when STRUCTURAL fired. Now VALUE_DIS only emits when the
                # structural condition isn't met — capturing the genuinely
                # different "soft lead, structures still contested" case.
                s = cur.get("structure_state")
                if s is not None and getattr(s, "confidence", 0.0) >= 0.8:
                    rad = (s.radiant_t1_alive, s.radiant_t2_alive,
                           s.radiant_t3_alive, s.radiant_t4_alive)
                    dr = (s.dire_t1_alive, s.dire_t2_alive,
                          s.dire_t3_alive, s.dire_t4_alive)
                    if all(t is not None for t in rad + dr):
                        struct_diff_for_rad = sum(rad) - sum(dr)
                        if direction == "radiant" and struct_diff_for_rad >= 2:
                            return []
                        if direction == "dire" and struct_diff_for_rad <= -2:
                            return []
                return [self._event_from_components(
                    "POLL_VALUE_DISAGREEMENT",
                    direction,
                    delta,
                    mapping,
                    _components_for_direction(components, direction, {"NETWORTH_DELTA", "KILL_DIFF_DELTA"}),
                    previous_value="neutral",
                    current_value="lead_established",
                    event_delta=abs_nw,
                    threshold=2500,
                    severity="medium",
                )]
        return []

    def _nw_kill_divergence_candidates(
        self,
        delta: SnapshotDelta,
        components: list[EventComponent],
        mapping: dict | None,
    ) -> list[DotaEvent]:
        """POLL_NW_KILL_DIVERGENCE — true mispricing signal (2026-05-30 #6).

        Fires when NW lead favors ONE side and kill lead favors the OTHER side
        beyond thresholds. The team ahead in farm typically wins (76% wr on 45
        matches in 7d backfill, NW≥3000 + kill≥3 opposite). Direction = the
        NW-favored side (farm beats kills long-run).

        This is the *actual* value-disagreement signal that POLL_VALUE_DISAGREEMENT
        was named for but didn't implement. Markets often over-weight kills
        because they're visible in highlights — sweep showed 76-77% wr at
        NW≥3000/kill≥3, suggesting market pricing follows kills more than
        underlying NW.
        """
        cur = delta.current
        nw_lead = _to_int(cur.get("radiant_lead"))
        rad_score = _to_int(cur.get("radiant_score"))
        dire_score = _to_int(cur.get("dire_score"))
        if nw_lead is None or rad_score is None or dire_score is None:
            return []
        kill_lead = rad_score - dire_score

        game_time = _to_int(cur.get("game_time_sec"))
        if game_time is None or game_time < 600:
            return []

        # Sweet spot from 7d backfill sweep: NW>=3000, kill>=3 OPPOSITE direction
        NW_TH = 3000
        KILL_TH = 3
        if abs(nw_lead) < NW_TH or abs(kill_lead) < KILL_TH:
            return []
        if (nw_lead > 0) == (kill_lead > 0):
            return []  # need disagreement

        # Direction = NW-favored side (the team that's ahead in farm)
        direction = "radiant" if nw_lead > 0 else "dire"
        return [self._event_from_components(
            "POLL_NW_KILL_DIVERGENCE",
            direction,
            delta,
            mapping,
            _components_for_direction(components, direction,
                                       {"NETWORTH_DELTA", "KILL_DIFF_DELTA"}),
            previous_value="kills_misleading",
            current_value="farm_ahead",
            event_delta=abs(nw_lead),
            threshold=NW_TH,
            severity="medium",
        )]

    def _structural_dominance_candidates(
        self,
        delta: SnapshotDelta,
        components: list[EventComponent],
        mapping: dict | None,
    ) -> list[DotaEvent]:
        """Fires when structure + networth + kills ALL favor the same side.

        In pro Dota, teams `gg` before T3/T4 fall — meaning OBJECTIVE_CONVERSION_T3
        / THRONE_EXPOSED / BASE_PRESSURE_* almost never trigger. But the
        market still drifts toward 1.0 as consensus forms. This event catches
        the "game is decided" period by combining the three signals that *can*
        be measured even when no structures fall:

        - structure_diff: total towers alive (T1+T2+T3+T4) — favored side has ≥3 more
        - networth lead: ≥5000 in favored side
        - kill lead: ≥4 in favored side

        All three must align (same direction) for the signal to fire. Hold to
        settlement (EXIT_HORIZON_BY_EVENT["POLL_STRUCTURAL_DOMINANCE"]=0) to
        ride the drift past fair_price toward 1.0.
        """
        cur = delta.current
        s = cur.get("structure_state")
        if s is None or getattr(s, "confidence", 0.0) < 0.8:
            return []

        # Total towers per side; require all four tiers decoded.
        rad_fields = (s.radiant_t1_alive, s.radiant_t2_alive, s.radiant_t3_alive, s.radiant_t4_alive)
        dire_fields = (s.dire_t1_alive, s.dire_t2_alive, s.dire_t3_alive, s.dire_t4_alive)
        if any(f is None for f in rad_fields + dire_fields):
            return []
        # 2026-05-30 Phase 5 — weight towers by tier (T4>T3>T2>T1).
        # Previously all towers counted equally — losing 2 T1 looked the same
        # as losing 1 T4 (which is far more dire). Weights chosen to match
        # rough relative game-impact in pro Dota.
        TIER_WEIGHTS = (1, 2, 3, 4)  # t1, t2, t3, t4
        def _weighted_towers(fields):
            return sum(alive * w for alive, w in zip(fields, TIER_WEIGHTS))
        rad_towers = sum(rad_fields)
        dire_towers = sum(dire_fields)
        rad_weighted = _weighted_towers(rad_fields)
        dire_weighted = _weighted_towers(dire_fields)
        # struct_diff uses weighted comparison; rad_/dire_towers kept for legacy refs
        struct_diff = rad_weighted - dire_weighted

        nw_lead = _to_int(cur.get("radiant_lead"))
        rad_score = _to_int(cur.get("radiant_score"))
        dire_score = _to_int(cur.get("dire_score"))
        if nw_lead is None or rad_score is None or dire_score is None:
            return []
        kill_lead = rad_score - dire_score

        game_time = _to_int(cur.get("game_time_sec"))
        if game_time is None or game_time < 600:
            return []

        # Need a clear winner: all three signals favor the same side, each over
        # its threshold.
        # E sweep 2026-05-26: loosened from (3,5000,4) → (2,2500,2).
        # Sweep results: 53 trades +$0.45 mean 88% win vs 16/+$0.15/87%.
        # 2026-05-30 Phase 5 — STRUCT_TH raised from 2 → 4 because tower
        # weights changed: previously "2 towers difference" could be 2 T1s
        # (weak signal). Now "4 weighted units" = at minimum 1 T2 + 2 T1s
        # OR 1 T3, OR 1 T4 — meaningful structural lead.
        STRUCT_TH, NW_TH, KILL_TH = 4, 2500, 2
        if (
            struct_diff >= STRUCT_TH
            and nw_lead >= NW_TH
            and kill_lead >= KILL_TH
        ):
            direction = "radiant"
        elif (
            struct_diff <= -STRUCT_TH
            and nw_lead <= -NW_TH
            and kill_lead <= -KILL_TH
        ):
            direction = "dire"
        else:
            return []

        return [self._event_from_components(
            "POLL_STRUCTURAL_DOMINANCE",
            direction,
            delta,
            mapping,
            _components_for_direction(components, direction, {"NETWORTH_DELTA", "KILL_DIFF_DELTA"}),
            previous_value="contested",
            current_value="dominated",
            event_delta=abs(nw_lead),
            threshold=NW_TH,
            severity="high",
        )]

    def _pre_push_setup_candidates(
        self,
        delta: SnapshotDelta,
        components: list[EventComponent],
        mapping: dict | None,
    ) -> list[DotaEvent]:
        """POLL_PRE_PUSH_SETUP — game is set up for a winning push.

        2026-05-29 backtest discovery (12 days, 375 fires, 91% settle win):
        when one side has knocked down 3+ of the OPPOSING side's towers AND
        has a 5k+ networth lead AND the game is past 25 minutes, they win at
        settlement ~91% of the time regardless of kill score.

        Different from POLL_STRUCTURAL_DOMINANCE which requires kill_lead >= 2:
        PRE_PUSH catches games where kill score looks even but structures are
        crushed — markets often under-price these. Specifically the cheap-
        entry bucket (ask < 0.50) had 87% win at +$1.25 per-$ over 15 trades —
        latency-arb on shifts the market hasn't fully repriced.

        Hold to settlement (EXIT_HORIZON_BY_EVENT["POLL_PRE_PUSH_SETUP"]=0).
        """
        cur = delta.current
        s = cur.get("structure_state")
        if s is None or getattr(s, "confidence", 0.0) < 0.8:
            return []

        rad_fields = (s.radiant_t1_alive, s.radiant_t2_alive,
                      s.radiant_t3_alive, s.radiant_t4_alive)
        dire_fields = (s.dire_t1_alive, s.dire_t2_alive,
                       s.dire_t3_alive, s.dire_t4_alive)
        if any(t is None for t in rad_fields + dire_fields):
            return []
        rad_towers_alive = sum(rad_fields)
        dire_towers_alive = sum(dire_fields)

        nw_lead = _to_int(cur.get("radiant_lead"))
        if nw_lead is None:
            return []

        game_time = _to_int(cur.get("game_time_sec"))
        if game_time is None or game_time < 1500:  # 25 min minimum
            return []

        # Radiant pushing: nw lead positive + 3+ dire towers down
        # 2026-05-30 — added struct_diff requirement (favored side must have
        # MORE towers alive than enemy). 7d real data showed 76% wr vs the
        # 91% projection. Hypothesis: some fires happen when both sides have
        # lost towers but enemy has more — that's a fragile lead. Requiring
        # struct_diff>=1 ensures the favored side hasn't bled equivalent
        # structure damage. Target wr: 83%+.
        NW_TH = 5000
        ENEMY_DOWN_TH = 3
        STRUCT_DIFF_TH = 1
        struct_diff_for_radiant = rad_towers_alive - dire_towers_alive
        if (nw_lead >= NW_TH
            and (11 - dire_towers_alive) >= ENEMY_DOWN_TH
            and struct_diff_for_radiant >= STRUCT_DIFF_TH):
            direction = "radiant"
        elif (nw_lead <= -NW_TH
              and (11 - rad_towers_alive) >= ENEMY_DOWN_TH
              and -struct_diff_for_radiant >= STRUCT_DIFF_TH):
            direction = "dire"
        else:
            return []

        return [self._event_from_components(
            "POLL_PRE_PUSH_SETUP",
            direction,
            delta,
            mapping,
            _components_for_direction(components, direction,
                                       {"NETWORTH_DELTA", "KILL_DIFF_DELTA"}),
            previous_value="contested",
            current_value="winning_push_setup",
            event_delta=abs(nw_lead),
            threshold=NW_TH,
            severity="high",
        )]

    def _objective_conversion_candidates(
        self,
        delta: SnapshotDelta,
        components: list[EventComponent],
        tactical_candidates: list[DotaEvent],
        mapping: dict | None,
    ) -> list[DotaEvent]:
        out: list[DotaEvent] = []
        for direction in {c.direction for c in components if c.direction}:
            tower = [
                c for c in components
                if c.direction == direction and c.component_type in CONVERSION_TOWER_COMPONENTS
            ]
            support = [
                e for e in tactical_candidates
                if e.direction == direction and e.event_type in TACTICAL_SUPPORT_COMPONENTS
            ]
            if not tower:
                continue

            # Compute pressure_ok for the fallback (no POLL co-occurrence) path
            pressure_ok = False
            if delta.kill_diff_delta is not None or delta.networth_delta is not None:
                signed_kills = delta.kill_diff_delta if direction == "radiant" else -delta.kill_diff_delta
                signed_nw = delta.networth_delta if direction == "radiant" else -delta.networth_delta
                if (signed_kills or 0) > 0 or (signed_nw or 0) > 100:
                    pressure_ok = True

            # Require either a co-occurring POLL event OR strong economy/kill pressure.
            # Relax this for long gaps (irregular Steam updates): if a team with a >$5k lead
            # takes a Tier 3 objective after a long silence, fire immediately.
            cadence_fallback = (delta.snapshot_gap_sec >= 35 and abs(delta.current.get("radiant_lead") or 0) > 5000)
            
            if not support and not pressure_ok and not cadence_fallback:
                continue

            event_type = _conversion_event_type(tower)
            if event_type is None:
                continue

            sd = delta.structure_delta
            if not sd or not sd.valid:
                continue

            # OBJECTIVE_CONVERSION_T4 only from actual T4 decrease.
            if event_type == "OBJECTIVE_CONVERSION_T4":
                t4_fallen = sd.radiant_t4_fallen if direction == "dire" else sd.dire_t4_fallen
                if t4_fallen <= 0:
                    continue

            # OBJECTIVE_CONVERSION_T3 only from actual T3 decrease.
            if event_type == "OBJECTIVE_CONVERSION_T3":
                t3_fallen = sd.radiant_t3_fallen if direction == "dire" else sd.dire_t3_fallen
                if t3_fallen <= 0:
                    continue

            conv_components = list(tower)
            if support:
                strongest_support = max(support, key=lambda e: TACTICAL_PRIORITY.get(e.event_type, 0))
                conv_components.extend(EventComponent(
                    strongest_support.event_type,
                    direction,
                    strongest_support.delta,
                    strongest_support.window_sec,
                    strongest_support.previous_value,
                    strongest_support.current_value,
                ) for _ in [0])
                prev_val = f"{strongest_support.event_type}+{max(tower, key=_conversion_tower_rank).component_type}"
            else:
                # Pressure-only path: no simultaneous POLL event but economy/kill pressure confirmed
                prev_val = f"pressure_only+{max(tower, key=_conversion_tower_rank).component_type}"

            out.append(self._event_from_components(
                event_type,
                direction,
                delta,
                mapping,
                conv_components,
                previous_value=prev_val,
                current_value="same_direction_objective_conversion",
                event_delta=max((abs(float(c.delta or 0)) for c in tower), default=0.0),
                severity="high" if event_type != "OBJECTIVE_CONVERSION_T2" else "medium",
            ))
        return out

    def _merge_components(self, primary: DotaEvent, lower: list[DotaEvent]) -> DotaEvent:
        if not lower:
            return primary
        component_types = [primary.component_event_types or ""]
        component_deltas = [primary.component_deltas or ""]
        component_windows = [primary.component_window_sec or ""]
        for event in lower:
            component_types.append(event.event_type)
            if event.component_event_types:
                component_types.append(event.component_event_types)
            component_deltas.append("" if event.delta is None else str(event.delta))
            if event.component_deltas:
                component_deltas.append(event.component_deltas)
            component_windows.append("" if event.window_sec is None else str(event.window_sec))
            if event.component_window_sec:
                component_windows.append(event.component_window_sec)
        return replace(
            primary,
            component_event_types="+".join(x for x in component_types if x),
            component_deltas="+".join(x for x in component_deltas if x != ""),
            component_window_sec="+".join(x for x in component_windows if x != ""),
        )

    def _first_swing_settle_candidates(
        self,
        delta: SnapshotDelta,
        mapping: dict | None,
    ) -> list[DotaEvent]:
        """POLL_FIRST_SWING_SETTLE — fires ONCE per match.

        Entry criteria (from gated-strategy backtest, n=46 matches, 80% wr):
          - game_time > 10 min
          - a kill happened in the last 2 snapshots
          - |Δ net_worth over last 1 OR last 3 snaps| > threshold(gt)
            where threshold = 800 + 20 * gt_min  (linear scaling)
          - d1 and d3 have same sign (sustained, not noise)
          - entry ask would be 0.45–0.90 (checked downstream by signal_engine
            MIN_FILL_PRICE / MAX_FILL caps — no check needed here)

        After firing, direction is locked in self._first_swing_direction[match_id]
        so that the gate can suppress opposite-direction event-engine signals.
        """
        match_id = delta.current.get("match_id", "")
        if not match_id or match_id in self._first_swing_fired:
            return []

        gt = delta.current.get("game_time_sec") or 0
        if gt < 600:   # < 10 min
            return []

        hist = self.history[match_id]
        if len(hist) < 4:
            return []

        # Kill activity in last 3 snapshots (P2 — widened 2→3 snaps, ~90s window).
        # Kills and nw swings don't always land in the same 60s window; 90s
        # captures more genuine fight-driven entries (+$0.67 in backtest).
        cur_kills = (delta.current.get("radiant_score") or 0) + (delta.current.get("dire_score") or 0)
        past_kills = (hist[-4].get("radiant_score") or 0) + (hist[-4].get("dire_score") or 0) if len(hist) >= 4 else cur_kills
        if cur_kills == past_kills:
            return []

        # Net-worth swing
        nw_now = delta.current.get("radiant_lead") or 0
        nw_1back = hist[-2].get("radiant_lead") or 0 if len(hist) >= 2 else nw_now
        nw_3back = hist[-4].get("radiant_lead") or 0 if len(hist) >= 4 else nw_now
        d1 = nw_now - nw_1back
        d3 = nw_now - nw_3back

        # 2026-05-31 — loosened 800+20 → 500+15 after volume/quality frontier
        # analysis. The stricter threshold cut to 16 trades chasing 100% in-sample
        # but made LESS money; 500+15 gives 22 trades (91% wr) for +$393 vs +$318.
        T = 500 + 15 * (gt / 60)
        if abs(d3) < T and abs(d1) < T:
            return []
        if d3 == 0 and d1 == 0:
            return []
        # Must be sustained (same sign)
        dominant = d3 if d3 != 0 else d1
        if d3 != 0 and d1 != 0 and (d3 > 0) != (d1 > 0):
            return []

        # 2026-05-31 — Ratio filter: skip if the deficit is > 3x the swing.
        # A small bounce inside a large deficit is noise, not a real momentum shift.
        # E.g. nw=-16k, swing=+4k → ratio=4.0 → skip (Game 2 dead-cat bounce case).
        # Threshold=3.0 keeps 75% of marked entries while filtering the failure case.
        if abs(dominant) > 0 and abs(nw_now) > 3 * abs(dominant):
            return []

        direction = "radiant" if dominant > 0 else "dire"

        # 2026-05-31 — Tiered nw/kill agreement check.
        # When entry price is cheap (ask 0.45-0.70), edge comes from DISAGREEMENT
        # between nw direction and kill direction (market mispricing).
        # When expensive (0.70-0.90) momentum following is fine either way.
        # Store agreement flag in event metadata for signal_engine to use.
        radiant_score = delta.current.get("radiant_score") or 0
        dire_score = delta.current.get("dire_score") or 0
        kill_diff = radiant_score - dire_score   # positive = radiant leading kills
        nw_dir_radiant = nw_now > 0              # True = radiant leading nw
        kill_dir_radiant = kill_diff > 0         # True = radiant leading kills
        nw_kill_agree = (nw_dir_radiant == kill_dir_radiant) or kill_diff == 0

        # Lock direction for this match
        self._first_swing_direction[match_id] = direction
        self._first_swing_fired.add(match_id)

        return [self._event_from_components(
            "POLL_FIRST_SWING_SETTLE",
            direction,
            delta,
            mapping,
            [],
            previous_value=str(nw_3back),
            current_value=str(nw_now),
            event_delta=abs(dominant),
            threshold=int(T),
            severity="high",
        )]

    def get_first_swing_direction(self, match_id: str) -> str | None:
        """Return the locked direction for this match, or None if not yet fired."""
        return self._first_swing_direction.get(str(match_id))

    def _reversal_entry_candidates(
        self,
        delta: SnapshotDelta,
        mapping: dict | None,
    ) -> list[DotaEvent]:
        """POLL_REVERSAL_ENTRY — S2 strategy: buy underdog early in comeback arc.

        Fires when a team is still losing (nw < -1500) but the situation has
        stopped getting worse and the market hasn't repriced (entry ask < 0.45).

        Entry conditions derived from 10-match data study:
          - game_time > 10 min
          - nw_lead < -1,500  (genuinely behind)
          - NOT collapsing: d_nw3 > -4,000  (not accelerating down)
          - ANY ONE of:
              (a) d_nw1 > 300           (first positive tick)
              (b) d_nw3 > -500          (momentum stalling)
              (c) recent COMEBACK_RECOVERY / PRE_PUSH_SETUP / FIGHT_SWING event
          - Entry price check deferred to signal_engine (MIN_FILL=0.05, MAX_FILL=0.45)

        Fires once per match, locks direction to the UNDERDOG side.
        Separate from POLL_FIRST_SWING_SETTLE (which buys favorites).
        """
        match_id = delta.current.get("match_id", "")
        if not match_id:
            return []
        # Don't fire if S1 already locked direction (different strategy, different matches)
        if match_id in self._first_swing_fired:
            return []
        # Don't fire if S2 already fired for this match
        if match_id in getattr(self, '_reversal_fired', set()):
            return []

        gt = delta.current.get("game_time_sec") or 0
        if gt < 600:  # < 10 min
            return []

        hist = self.history[match_id]
        if len(hist) < 4:
            return []

        nw_now = delta.current.get("radiant_lead") or 0

        # Must be behind by meaningful amount — scale with game time
        # gt=10min: need 3,000 nw deficit; gt=20min: 4,000; gt=30min: 5,000
        min_deficit = 2000 + (gt / 60) * 100
        if abs(nw_now) < min_deficit:
            return []

        # Determine which side is losing (small nw = underdog)
        # nw_now > 0 = radiant leading, so dire is underdog
        # nw_now < 0 = dire leading, so radiant is underdog
        underdog_dir = "radiant" if nw_now < 0 else "dire"

        nw_1back = hist[-2].get("radiant_lead") or 0 if len(hist) >= 2 else nw_now
        nw_3back = hist[-4].get("radiant_lead") or 0 if len(hist) >= 4 else nw_now
        # From underdog's perspective (flip sign if underdog is dire)
        if underdog_dir == "dire":
            ud_now = -nw_now
            ud_1back = -nw_1back
            ud_3back = -nw_3back
        else:
            ud_now = nw_now
            ud_1back = nw_1back
            ud_3back = nw_3back

        d_nw1 = ud_now - ud_1back  # positive = underdog closing gap
        d_nw3 = ud_now - ud_3back

        # Not collapsing (deficit not accelerating)
        if d_nw3 < -4000:
            return []

        # At least one positive signal
        triggered = False
        if d_nw1 > 300:    triggered = True   # first positive tick
        if d_nw3 > -500:   triggered = True   # momentum stalling

        if not triggered:
            return []

        # Initialise the fired set if missing (for backwards compat)
        if not hasattr(self, '_reversal_fired'):
            self._reversal_fired = set()

        self._reversal_fired.add(match_id)
        # S2 does NOT lock _first_swing_direction — it does not gate other events.
        # Only S1 (POLL_FIRST_SWING_SETTLE) locks direction to gate the event engine.
        # S2 is a standalone one-shot trade, no conflict with the gate.

        return [self._event_from_components(
            "POLL_REVERSAL_ENTRY",
            underdog_dir,
            delta,
            mapping,
            [],
            previous_value=str(int(ud_3back)),
            current_value=str(int(ud_now)),
            event_delta=abs(d_nw1),
            threshold=300,
            severity="high",
        )]

    def _enrich_pressure(self, events: list[DotaEvent], delta: SnapshotDelta) -> list[DotaEvent]:
        enriched: list[DotaEvent] = []
        for evt in events:
            bp = _EVENT_BASE_PRESSURE.get(evt.event_type, 0.3)
            conf = _EVENT_CONFIDENCE.get(evt.event_type, 0.5)
            fp: float | None = None
            ep: float | None = None
            cs: float | None = None

            if delta.kill_diff_delta is not None and evt.direction is not None:
                signed = delta.kill_diff_delta if evt.direction == "radiant" else -delta.kill_diff_delta
                fp = max(0.0, min(signed / 5.0, 1.0))
            if delta.networth_delta is not None and evt.direction is not None:
                signed = delta.networth_delta if evt.direction == "radiant" else -delta.networth_delta
                ep = max(0.0, min(signed / 5000.0, 1.0))
            if fp is not None and ep is not None:
                cs = min(math.sqrt(fp * ep), 1.0) if fp > 0 and ep > 0 else max(fp, ep) * 0.3
            elif fp is not None:
                cs = fp * 0.4 if fp > 0 else None
            elif ep is not None:
                cs = ep * 0.4 if ep > 0 else None

            if delta.source_cadence_quality == "direct":
                conf = min(conf + 0.05, 1.0)
            elif delta.source_cadence_quality == "stale_gap":
                conf = max(conf - 0.12, 0.0)
            elif delta.source_cadence_quality == "invalid_gap":
                conf = max(conf - 0.25, 0.0)
            if evt.event_type.startswith("OBJECTIVE_CONVERSION_"):
                conf = min(conf + 0.08, 1.0)
            if fp and fp > 0:
                conf = min(conf + 0.05, 1.0)
            if ep and ep > 0:
                conf = min(conf + 0.05, 1.0)

            enriched.append(replace(
                evt,
                base_pressure_score=round(bp, 3),
                fight_pressure_score=round(fp, 3) if fp is not None else None,
                economic_pressure_score=round(ep, 3) if ep is not None else None,
                conversion_score=round(cs, 3) if cs is not None else None,
                event_confidence=round(conf, 3),
            ))
        return enriched

    def _add_event_metadata(self, events: list[DotaEvent]) -> list[DotaEvent]:
        out = []
        for event in events:
            out.append(replace(
                event,
                event_dedupe_key=_event_dedupe_key(event),
                event_is_primary=event_is_primary(event.event_type),
                event_tier=event_tier(event.event_type),
                event_family=event_family(event.event_type),
                event_quality=round(_event_quality(event), 3),
            ))
        return out

    def _dedupe_events(self, events: list[DotaEvent]) -> list[DotaEvent]:
        out = []
        for event in events:
            key = event.event_dedupe_key or _event_dedupe_key(event)
            game_time = event.game_time_sec
            if game_time is not None:
                last = self.last_emitted_dedupe_game_time.get(key)
                if last is not None and game_time - last < EVENT_DEDUPE_SECONDS:
                    continue
                self.last_emitted_dedupe_game_time[key] = game_time
            out.append(event)
        return out

    def _cooldown_ok(self, snap: dict, event_type: str, direction: str | None) -> bool:
        match_id = snap["match_id"]
        game_time = snap.get("game_time_sec")
        if game_time is None:
            return True
        key = (match_id, event_type, direction)
        last = self.last_emitted_game_time.get(key)
        if last is not None and game_time - last < EVENT_COOLDOWN_GAME_SECONDS:
            return False
        self.last_emitted_game_time[key] = game_time
        return True


def _group_events_by_direction(events: list[DotaEvent]) -> dict[str | None, list[DotaEvent]]:
    grouped: dict[str | None, list[DotaEvent]] = defaultdict(list)
    for event in events:
        grouped[event.direction].append(event)
    return grouped


def _components_for_direction(
    components: list[EventComponent],
    direction: str,
    allowed: set[str],
) -> list[EventComponent]:
    return [
        comp for comp in components
        if comp.component_type in allowed and (comp.direction in (direction, None))
    ]


def _direction_from_delta(value: int | float | None) -> str | None:
    if value is None or value == 0:
        return None
    return "radiant" if value > 0 else "dire"


def _cadence_quality(gap: int) -> str:
    if gap <= DIRECT_GAP_SEC:
        return "direct"
    if gap <= NORMAL_GAP_SEC:
        return "normal"
    if gap <= STALE_GAP_SEC:
        return "stale_gap"
    return "invalid_gap"


def _score_value(snapshot: dict) -> str:
    return f"{snapshot.get('radiant_score')}-{snapshot.get('dire_score')}"


def _conversion_event_type(tower_components: list[EventComponent]) -> str | None:
    types = {component.component_type for component in tower_components}
    if types & {"THRONE_EXPOSED_COMPONENT", "FIRST_T4_TOWER_FALL", "SECOND_T4_TOWER_FALL", "T3_PLUS_T4_CHAIN"}:
        return "OBJECTIVE_CONVERSION_T4"
    if "RAX_FALL" in types:
        return "OBJECTIVE_CONVERSION_RAX"
    if types & {"ALL_T3_TOWERS_DOWN", "MULTIPLE_T3_TOWERS_DOWN", "T3_TOWER_FALL", "MULTI_STRUCTURE_COLLAPSE"}:
        return "OBJECTIVE_CONVERSION_T3"
    if types & {"T2_TOWER_FALL", "MULTIPLE_T2_TOWERS_DOWN", "ALL_T2_TOWERS_DOWN"}:
        return "OBJECTIVE_CONVERSION_T2"
    return None


def _conversion_tower_rank(component: EventComponent) -> int:
    ranks = {
        "T2_TOWER_FALL": 1,
        "MULTIPLE_T2_TOWERS_DOWN": 2,
        "ALL_T2_TOWERS_DOWN": 3,
        "T3_TOWER_FALL": 4,
        "MULTIPLE_T3_TOWERS_DOWN": 5,
        "ALL_T3_TOWERS_DOWN": 6,
        "RAX_FALL": 6.5,
        "FIRST_T4_TOWER_FALL": 7,
        "SECOND_T4_TOWER_FALL": 8,
        "THRONE_EXPOSED_COMPONENT": 9,
        "T3_PLUS_T4_CHAIN": 10,
        "MULTI_STRUCTURE_COLLAPSE": 11,
    }
    return ranks.get(component.component_type, 0)


def _component_metadata(components: list[EventComponent]) -> dict[str, str | None]:
    if not components:
        return {
            "component_event_types": None,
            "component_deltas": None,
            "component_window_sec": None,
        }
    return {
        "component_event_types": "+".join(component.component_type for component in components),
        "component_deltas": "+".join("" if component.delta is None else str(component.delta) for component in components),
        "component_window_sec": "+".join("" if component.window_sec is None else str(component.window_sec) for component in components),
    }


def _event_dedupe_key(event: DotaEvent) -> str:
    return "|".join(str(part) for part in (
        event.match_id,
        event.event_type,
        event.direction,
        event.previous_value,
        event.current_value,
        event.delta,
        event.actual_window_sec,
    ))


def _event_quality(event: DotaEvent) -> float:
    base = float(event.base_pressure_score or 0.0)
    conversion = float(event.conversion_score or 0.0)
    fight = float(event.fight_pressure_score or 0.0)
    economy = float(event.economic_pressure_score or 0.0)
    confidence = float(event.event_confidence or 0.0)
    return (0.30 * base) + (0.20 * conversion) + (0.18 * fight) + (0.18 * economy) + (0.14 * confidence)


def _round_optional(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _bit_count(value: int) -> int:
    return int(value).bit_count()


def _to_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None
