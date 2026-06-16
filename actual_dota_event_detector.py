from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from actual_dota_event_types import ActualDotaEvent, ActualDotaEventType
from structure_state import decode_structure_state, diff_structure_state


_NAMESPACE = uuid.UUID("11111111-2222-3333-4444-555555555559")
_NETWORTH_CHANGE_MIN = 1000
_NETWORTH_FLIP_MIN_SIDE = 1000
_NETWORTH_FLIP_MIN_SWING = 2500
_MULTI_KILL_WINDOW_SEC = 90


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _side_from_delta(delta: int) -> str:
    if delta > 0:
        return "radiant"
    if delta < 0:
        return "dire"
    return ""


def _swing_threshold(game_time_sec: int | None) -> int:
    gt = game_time_sec or 0
    if gt < 1200:
        return 1800
    if gt < 2100:
        return 2800
    return 4000


@dataclass(frozen=True)
class _SnapshotState:
    match_id: str
    game_time_sec: int | None
    received_at_ns: int
    radiant_lead: int | None
    radiant_score: int | None
    dire_score: int | None
    game_over: bool
    raw: dict


class ActualDotaEventDetector:
    """Factual TopLive transition detector.

    This deliberately does not infer Roshan, Aegis, deaths, team wipes, or trade
    intent. It emits only state transitions present in consecutive TopLive
    snapshots.
    """

    def __init__(self) -> None:
        self._prev: dict[str, _SnapshotState] = {}

    def observe(self, game: Mapping[str, Any]) -> list[ActualDotaEvent]:
        if game.get("data_source") != "top_live":
            return []
        match_id = str(game.get("match_id") or game.get("lobby_id") or "")
        if not match_id:
            return []
        cur = self._state(game, match_id)
        prev = self._prev.get(match_id)
        self._prev[match_id] = cur
        if prev is None:
            return []
        if prev.game_time_sec is not None and cur.game_time_sec is not None:
            if cur.game_time_sec < prev.game_time_sec:
                return []
        if prev.received_at_ns and cur.received_at_ns and cur.received_at_ns <= prev.received_at_ns:
            return []

        events: list[ActualDotaEvent] = []
        events.extend(self._score_events(prev, cur))
        events.extend(self._networth_events(prev, cur))
        events.extend(self._structure_events(prev, cur))
        if cur.game_over and not prev.game_over:
            events.append(self._event(cur, "GAME_ENDED", side="", previous_value=False, current_value=True))
        return events

    def _state(self, game: Mapping[str, Any], match_id: str) -> _SnapshotState:
        return _SnapshotState(
            match_id=match_id,
            game_time_sec=_to_int(game.get("game_time_sec")),
            received_at_ns=int(game.get("received_at_ns") or 0),
            radiant_lead=_to_int(game.get("radiant_lead")),
            radiant_score=_to_int(game.get("radiant_score")),
            dire_score=_to_int(game.get("dire_score")),
            game_over=bool(game.get("game_over")),
            raw=dict(game),
        )

    def _window_sec(self, prev: _SnapshotState, cur: _SnapshotState) -> int | None:
        if prev.game_time_sec is not None and cur.game_time_sec is not None:
            return max(0, cur.game_time_sec - prev.game_time_sec)
        if prev.received_at_ns and cur.received_at_ns:
            return max(0, int((cur.received_at_ns - prev.received_at_ns) / 1_000_000_000))
        return None

    def _score_events(self, prev: _SnapshotState, cur: _SnapshotState) -> list[ActualDotaEvent]:
        if None in (prev.radiant_score, prev.dire_score, cur.radiant_score, cur.dire_score):
            return []
        r_delta = int(cur.radiant_score - prev.radiant_score)
        d_delta = int(cur.dire_score - prev.dire_score)
        if r_delta < 0 or d_delta < 0:
            return []
        if r_delta == 0 and d_delta == 0:
            return []

        side = "both"
        if r_delta > d_delta:
            side = "radiant"
        elif d_delta > r_delta:
            side = "dire"
        events = [
            self._event(
                cur,
                "TEAM_KILL_SCORE_CHANGE",
                side=side,
                previous_value={"radiant_score": prev.radiant_score, "dire_score": prev.dire_score},
                current_value={"radiant_score": cur.radiant_score, "dire_score": cur.dire_score},
                delta=r_delta + d_delta,
                radiant_lead_before=prev.radiant_lead,
                radiant_score_before=prev.radiant_score,
                dire_score_before=prev.dire_score,
                details=json.dumps({"radiant_kills_delta": r_delta, "dire_kills_delta": d_delta}, sort_keys=True),
            )
        ]
        window = self._window_sec(prev, cur)
        if window is not None and window <= _MULTI_KILL_WINDOW_SEC:
            if r_delta >= 3 and d_delta <= 1:
                events.append(self._event(
                    cur, "MULTI_KILL_WINDOW", side="radiant", delta=r_delta, window_sec=window,
                    radiant_lead_before=prev.radiant_lead,
                    radiant_score_before=prev.radiant_score,
                    dire_score_before=prev.dire_score,
                ))
            if d_delta >= 3 and r_delta <= 1:
                events.append(self._event(
                    cur, "MULTI_KILL_WINDOW", side="dire", delta=d_delta, window_sec=window,
                    radiant_lead_before=prev.radiant_lead,
                    radiant_score_before=prev.radiant_score,
                    dire_score_before=prev.dire_score,
                ))
        return events

    def _networth_events(self, prev: _SnapshotState, cur: _SnapshotState) -> list[ActualDotaEvent]:
        if prev.radiant_lead is None or cur.radiant_lead is None:
            return []
        delta = cur.radiant_lead - prev.radiant_lead
        if delta == 0:
            return []
        events: list[ActualDotaEvent] = []
        side = _side_from_delta(delta)
        window = self._window_sec(prev, cur)
        if abs(delta) >= _NETWORTH_CHANGE_MIN:
            events.append(self._event(
                cur,
                "NETWORTH_LEAD_CHANGE",
                side=side,
                previous_value=prev.radiant_lead,
                current_value=cur.radiant_lead,
                delta=delta,
                window_sec=window,
                networth_delta=delta,
                radiant_lead_before=prev.radiant_lead,
                radiant_score_before=prev.radiant_score,
                dire_score_before=prev.dire_score,
            ))
        if abs(delta) >= _swing_threshold(cur.game_time_sec):
            events.append(self._event(
                cur,
                "NETWORTH_SWING_WINDOW",
                side=side,
                previous_value=prev.radiant_lead,
                current_value=cur.radiant_lead,
                delta=delta,
                window_sec=window,
                networth_delta=delta,
                radiant_lead_before=prev.radiant_lead,
                radiant_score_before=prev.radiant_score,
                dire_score_before=prev.dire_score,
            ))
        if (
            prev.radiant_lead * cur.radiant_lead < 0
            and min(abs(prev.radiant_lead), abs(cur.radiant_lead)) >= _NETWORTH_FLIP_MIN_SIDE
            and abs(delta) >= _NETWORTH_FLIP_MIN_SWING
        ):
            events.append(self._event(
                cur,
                "NETWORTH_LEAD_FLIP",
                side="radiant" if cur.radiant_lead > 0 else "dire",
                previous_value=prev.radiant_lead,
                current_value=cur.radiant_lead,
                delta=delta,
                window_sec=window,
                networth_delta=delta,
                radiant_lead_before=prev.radiant_lead,
                radiant_score_before=prev.radiant_score,
                dire_score_before=prev.dire_score,
            ))
        return events

    def _structure_events(self, prev: _SnapshotState, cur: _SnapshotState) -> list[ActualDotaEvent]:
        prev_s = decode_structure_state(prev.raw)
        cur_s = decode_structure_state(cur.raw)
        delta = diff_structure_state(prev_s, cur_s)
        if not delta.valid:
            return []
        events: list[ActualDotaEvent] = []
        for team in ("radiant", "dire"):
            for tier in ("t2", "t3", "t4"):
                fallen = int(getattr(delta, f"{team}_{tier}_fallen", 0) or 0)
                if fallen <= 0:
                    continue
                side = "dire" if team == "radiant" else "radiant"
                before = getattr(delta, f"{team}_{tier}_before")
                after = getattr(delta, f"{team}_{tier}_after")
                events.append(self._event(
                    cur,
                    "TOWER_DESTROYED",
                    side=side,
                    previous_value=before,
                    current_value=after,
                    delta=fallen,
                    window_sec=self._window_sec(prev, cur),
                    structure_team=team,
                    structure_tier=tier.upper(),
                    source_field=delta.source_field,
                    confidence=delta.confidence,
                    radiant_lead_before=prev.radiant_lead,
                    radiant_score_before=prev.radiant_score,
                    dire_score_before=prev.dire_score,
                ))
                if before and after == 0:
                    events.append(self._event(
                        cur,
                        "TOWER_TIER_CLEARED",
                        side=side,
                        previous_value=before,
                        current_value=after,
                        delta=fallen,
                        window_sec=self._window_sec(prev, cur),
                        structure_team=team,
                        structure_tier=tier.upper(),
                        source_field=delta.source_field,
                        confidence=delta.confidence,
                        radiant_lead_before=prev.radiant_lead,
                        radiant_score_before=prev.radiant_score,
                        dire_score_before=prev.dire_score,
                    ))
        return events

    def _event(
        self,
        cur: _SnapshotState,
        event_type,
        *,
        side: str,
        previous_value: Any = None,
        current_value: Any = None,
        delta: int | None = None,
        window_sec: int | None = None,
        networth_delta: int | None = None,
        structure_team: str = "",
        structure_tier: str = "",
        source_field: str = "",
        confidence: float = 1.0,
        details: str = "",
        radiant_lead_before: int | None = None,
        radiant_score_before: int | None = None,
        dire_score_before: int | None = None,
    ) -> ActualDotaEvent:
        event_id = str(uuid.uuid5(
            _NAMESPACE,
            f"{cur.match_id}|{event_type}|{side}|{cur.game_time_sec}|{previous_value}|{current_value}|{delta}|{structure_team}|{structure_tier}",
        ))
        return ActualDotaEvent(
            event_id=event_id,
            event_type=ActualDotaEventType(event_type),
            match_id=cur.match_id,
            lobby_id=str(cur.raw.get("lobby_id") or ""),
            league_id=str(cur.raw.get("league_id") or ""),
            source="top_live",
            side=side,
            game_time_sec=cur.game_time_sec,
            received_at_ns=cur.received_at_ns,
            previous_value=previous_value,
            current_value=current_value,
            delta=delta,
            window_sec=window_sec,
            radiant_lead_before=radiant_lead_before,
            radiant_lead_after=cur.radiant_lead,
            radiant_score_before=radiant_score_before,
            radiant_score_after=cur.radiant_score,
            dire_score_before=dire_score_before,
            dire_score_after=cur.dire_score,
            networth_delta=networth_delta,
            structure_team=structure_team,
            structure_tier=structure_tier,
            source_field=source_field,
            confidence=confidence,
            details=details,
        )
