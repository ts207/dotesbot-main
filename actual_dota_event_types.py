from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


ActualDotaEventType = Literal[
    "TEAM_KILL_SCORE_CHANGE",
    "MULTI_KILL_WINDOW",
    "NETWORTH_LEAD_CHANGE",
    "NETWORTH_SWING_WINDOW",
    "NETWORTH_LEAD_FLIP",
    "TOWER_DESTROYED",
    "TOWER_TIER_CLEARED",
    "GAME_ENDED",
]


PRIMITIVE_EVENT_TYPES: set[str] = {
    "TEAM_KILL_SCORE_CHANGE",
    "MULTI_KILL_WINDOW",
    "NETWORTH_LEAD_CHANGE",
    "NETWORTH_SWING_WINDOW",
    "NETWORTH_LEAD_FLIP",
    "TOWER_DESTROYED",
    "TOWER_TIER_CLEARED",
    "GAME_ENDED",
}


@dataclass(frozen=True)
class ActualDotaEvent:
    event_id: str
    event_type: ActualDotaEventType
    match_id: str
    lobby_id: str
    league_id: str
    source: str
    side: str
    game_time_sec: int | None
    received_at_ns: int
    previous_value: Any = None
    current_value: Any = None
    delta: int | None = None
    window_sec: int | None = None
    radiant_lead_before: int | None = None
    radiant_lead_after: int | None = None
    radiant_score_before: int | None = None
    radiant_score_after: int | None = None
    dire_score_before: int | None = None
    dire_score_after: int | None = None
    networth_delta: int | None = None
    structure_team: str = ""
    structure_tier: str = ""
    source_field: str = ""
    confidence: float = 1.0
    details: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
