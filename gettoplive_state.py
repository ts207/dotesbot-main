from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Any


REQUIRED_TOP_LIVE_FIELDS = (
    "received_at_ns",
    "match_id",
    "game_time_sec",
    "radiant_lead",
    "radiant_score",
    "dire_score",
    "building_state",
    "tower_state",
)


@dataclass(frozen=True)
class TopLiveStateCheck:
    ok: bool
    reason: str
    missing_fields: tuple[str, ...] = ()


def _present(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def validate_top_live_state(game: Mapping[str, Any]) -> TopLiveStateCheck:
    """Validate the undelayed GetTopLive state needed by survival strategies."""
    if game.get("data_source") != "top_live":
        return TopLiveStateCheck(False, "not_top_live")

    missing = tuple(field for field in REQUIRED_TOP_LIVE_FIELDS if not _present(game.get(field)))
    if missing:
        return TopLiveStateCheck(False, "missing_top_live_state", missing)

    return TopLiveStateCheck(True, "ok")
