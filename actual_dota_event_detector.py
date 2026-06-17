from __future__ import annotations

import collections
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from actual_dota_event_types import ActualDotaEvent, ActualDotaEventType
from structure_state import decode_structure_state, diff_structure_state


_NAMESPACE = uuid.UUID("11111111-2222-3333-4444-555555555559")
_NETWORTH_CHANGE_MIN = 1000
_NETWORTH_FLIP_MIN_SIDE = 1000
_NETWORTH_FLIP_MIN_SWING = 2500
_MULTI_KILL_WINDOW_LIVE_SEC = int(os.getenv("MULTI_KILL_WINDOW_LIVE_SEC", "30"))
_MULTI_KILL_WINDOW_RESEARCH_SEC = int(os.getenv("MULTI_KILL_WINDOW_RESEARCH_SEC", "90"))


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
    snapshots, or over a rolling window for events like MULTI_KILL_WINDOW and
    NETWORTH_SWING_WINDOW.
    """

    def __init__(self) -> None:
        self._history: dict[str, collections.deque[_SnapshotState]] = collections.defaultdict(lambda: collections.deque(maxlen=500))
        self._emitted: dict[str, set[str]] = collections.defaultdict(set)

    def observe(self, game: Mapping[str, Any]) -> list[ActualDotaEvent]:
        if game.get("data_source") != "top_live":
            return []
        match_id = str(game.get("match_id") or game.get("lobby_id") or "")
        if not match_id:
            return []
            
        cur = self._state(game, match_id)
        history = self._history[match_id]
        
        prev = history[-1] if history else None
        
        if prev:
            if prev.game_time_sec is not None and cur.game_time_sec is not None:
                if cur.game_time_sec < prev.game_time_sec:
                    # Time went backwards (game restart/bug). Clear history.
                    history.clear()
                    self._emitted[match_id].clear()
                    prev = None
            elif prev.received_at_ns and cur.received_at_ns and cur.received_at_ns <= prev.received_at_ns:
                # Same frame or delayed packet
                return []
                
        history.append(cur)
        
        # Prune history to keep only max research window + 30s buffer
        max_age_sec = _MULTI_KILL_WINDOW_RESEARCH_SEC + 30
        while len(history) > 1:
            oldest = history[0]
            if oldest.game_time_sec is not None and cur.game_time_sec is not None:
                if cur.game_time_sec - oldest.game_time_sec > max_age_sec:
                    history.popleft()
                else:
                    break
            elif oldest.received_at_ns and cur.received_at_ns:
                if (cur.received_at_ns - oldest.received_at_ns) / 1e9 > max_age_sec:
                    history.popleft()
                else:
                    break
            else:
                break
                
        if not prev:
            return []

        events: list[ActualDotaEvent] = []
        events.extend(self._score_events_immediate(prev, cur))
        events.extend(self._networth_events_immediate(prev, cur))
        events.extend(self._structure_events(prev, cur))
        
        if cur.game_over and not prev.game_over:
            events.append(self._event(cur, "GAME_ENDED", side="", previous_value=False, current_value=True))
            
        # Rolling window events
        events.extend(self._rolling_window_events(history, cur, match_id))
        
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

    def _score_events_immediate(self, prev: _SnapshotState, cur: _SnapshotState) -> list[ActualDotaEvent]:
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
        return [
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

    def _networth_events_immediate(self, prev: _SnapshotState, cur: _SnapshotState) -> list[ActualDotaEvent]:
        if prev.radiant_lead is None or cur.radiant_lead is None:
            return []
        delta = cur.radiant_lead - prev.radiant_lead
        if delta == 0:
            return []
        events = []
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

    def _get_baseline(self, history: collections.deque[_SnapshotState], cur: _SnapshotState, target_window_sec: int) -> _SnapshotState | None:
        """Find the oldest snapshot that is still within target_window_sec of cur."""
        best = None
        for snap in reversed(history):
            window = self._window_sec(snap, cur)
            if window is not None and window <= target_window_sec:
                best = snap
            else:
                break
        return best

    def _rolling_window_events(self, history: collections.deque[_SnapshotState], cur: _SnapshotState, match_id: str) -> list[ActualDotaEvent]:
        events = []
        
        # 1. Multi Kill Window
        baseline_live = self._get_baseline(history, cur, _MULTI_KILL_WINDOW_LIVE_SEC)
        baseline_research = self._get_baseline(history, cur, _MULTI_KILL_WINDOW_RESEARCH_SEC)
        
        baselines = []
        if baseline_live:
            baselines.append((baseline_live, True))
        if baseline_research and baseline_research != baseline_live:
            baselines.append((baseline_research, False))
        
        for baseline, is_live in baselines:
            if not baseline:
                continue
            if None in (baseline.radiant_score, baseline.dire_score, cur.radiant_score, cur.dire_score):
                continue
            r_delta = int(cur.radiant_score - baseline.radiant_score)
            d_delta = int(cur.dire_score - baseline.dire_score)
            
            if r_delta < 0 or d_delta < 0:
                continue
                
            window = self._window_sec(baseline, cur)
            if window is None:
                continue
                
            side = None
            delta = 0
            if r_delta >= 3 and d_delta <= 1:
                side = "radiant"
                delta = r_delta
            elif d_delta >= 3 and r_delta <= 1:
                side = "dire"
                delta = d_delta
                
            if side:
                event_key = f"MULTI_KILL_{side}_{baseline.game_time_sec}_{cur.game_time_sec}_{delta}_{is_live}"
                if event_key not in self._emitted[match_id]:
                    self._emitted[match_id].add(event_key)
                    events.append(self._event(
                        cur, "MULTI_KILL_WINDOW", side=side, delta=delta, window_sec=window,
                        live_grade_event=is_live,
                        radiant_lead_before=baseline.radiant_lead,
                        radiant_score_before=baseline.radiant_score,
                        dire_score_before=baseline.dire_score,
                        details=json.dumps({
                            "radiant_kills_delta": r_delta,
                            "dire_kills_delta": d_delta,
                            "baseline_game_time_sec": baseline.game_time_sec,
                            "current_game_time_sec": cur.game_time_sec,
                            "rolling_window": True,
                            "live_grade_event": is_live,
                        }, sort_keys=True)
                    ))
        
        # 2. Networth Swing and Flip
        baseline_swing = baseline_live # use live window for networth swing
        if baseline_swing and baseline_swing.radiant_lead is not None and cur.radiant_lead is not None:
            delta = cur.radiant_lead - baseline_swing.radiant_lead
            side = _side_from_delta(delta)
            window = self._window_sec(baseline_swing, cur)
            
            if abs(delta) >= _swing_threshold(cur.game_time_sec):
                event_key = f"NW_SWING_{side}_{baseline_swing.game_time_sec}_{cur.game_time_sec}_{delta}"
                if event_key not in self._emitted[match_id]:
                    self._emitted[match_id].add(event_key)
                    events.append(self._event(
                        cur,
                        "NETWORTH_SWING_WINDOW",
                        side=side,
                        previous_value=baseline_swing.radiant_lead,
                        current_value=cur.radiant_lead,
                        delta=delta,
                        window_sec=window,
                        networth_delta=delta,
                        radiant_lead_before=baseline_swing.radiant_lead,
                        radiant_score_before=baseline_swing.radiant_score,
                        dire_score_before=baseline_swing.dire_score,
                        details=json.dumps({
                            "networth_delta": delta,
                            "baseline_radiant_lead": baseline_swing.radiant_lead,
                            "current_radiant_lead": cur.radiant_lead,
                            "rolling_window": True,
                            "live_grade_event": True,
                        }, sort_keys=True)
                    ))

            if (
                baseline_swing.radiant_lead * cur.radiant_lead < 0
                and min(abs(baseline_swing.radiant_lead), abs(cur.radiant_lead)) >= _NETWORTH_FLIP_MIN_SIDE
                and abs(delta) >= _NETWORTH_FLIP_MIN_SWING
            ):
                flip_side = "radiant" if cur.radiant_lead > 0 else "dire"
                event_key = f"NW_FLIP_{flip_side}_{baseline_swing.game_time_sec}_{cur.game_time_sec}_{delta}"
                if event_key not in self._emitted[match_id]:
                    self._emitted[match_id].add(event_key)
                    events.append(self._event(
                        cur,
                        "NETWORTH_LEAD_FLIP",
                        side=flip_side,
                        previous_value=baseline_swing.radiant_lead,
                        current_value=cur.radiant_lead,
                        delta=delta,
                        window_sec=window,
                        networth_delta=delta,
                        radiant_lead_before=baseline_swing.radiant_lead,
                        radiant_score_before=baseline_swing.radiant_score,
                        dire_score_before=baseline_swing.dire_score,
                        details=json.dumps({
                            "networth_delta": delta,
                            "baseline_radiant_lead": baseline_swing.radiant_lead,
                            "current_radiant_lead": cur.radiant_lead,
                            "rolling_window": True,
                            "live_grade_event": True,
                        }, sort_keys=True)
                    ))

        # limit emitted cache size
        if len(self._emitted[match_id]) > 500:
            self._emitted[match_id] = set(list(self._emitted[match_id])[-200:])
            
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
        live_grade_event: bool = True,
        networth_delta: int | None = None,
        structure_team: str = "",
        structure_tier: str = "",
        source_field: str = "",
        confidence: float = 1.0,
        radiant_lead_before: int | None = None,
        radiant_score_before: int | None = None,
        dire_score_before: int | None = None,
        details: str = "",
    ) -> ActualDotaEvent:
        event_id = str(uuid.uuid5(
            _NAMESPACE,
            f"{cur.match_id}|{event_type}|{cur.game_time_sec or cur.received_at_ns}|{side}|{delta or ''}|{previous_value or ''}|{current_value or ''}"
        ))
        return ActualDotaEvent(
            event_id=event_id,
            event_type=ActualDotaEventType(event_type),
            match_id=cur.match_id,
            lobby_id=cur.raw.get("lobby_id", ""),
            league_id=str(cur.raw.get("league_id") or ""),
            source=cur.raw.get("data_source", ""),
            side=side,
            game_time_sec=cur.game_time_sec,
            received_at_ns=cur.received_at_ns,
            previous_value=previous_value,
            current_value=current_value,
            delta=delta,
            window_sec=window_sec,
            live_grade_event=live_grade_event,
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
