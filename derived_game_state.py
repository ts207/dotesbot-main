from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from structure_state import decode_structure_state


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class DerivedGameState:
    flags: tuple[str, ...]
    phase_adjusted_networth_lead: int | None
    networth_side: str
    structure_advantage_side: str
    kill_diff_side: str

    def to_dict(self) -> dict:
        return {
            "flags": list(self.flags),
            "phase_adjusted_networth_lead": self.phase_adjusted_networth_lead,
            "networth_side": self.networth_side,
            "structure_advantage_side": self.structure_advantage_side,
            "kill_diff_side": self.kill_diff_side,
        }


def derive_game_state(game: Mapping[str, Any]) -> DerivedGameState:
    gt = _to_int(game.get("game_time_sec")) or 0
    lead = _to_int(game.get("radiant_lead"))
    r_score = _to_int(game.get("radiant_score"))
    d_score = _to_int(game.get("dire_score"))

    phase_divisor = 1.0
    if gt < 600:
        phase_divisor = 1.5
    elif gt > 1800:
        phase_divisor = 0.75
    phase_lead = None if lead is None else int(lead / phase_divisor)

    networth_side = ""
    if lead is not None:
        networth_side = "radiant" if lead > 0 else ("dire" if lead < 0 else "")

    kill_diff_side = ""
    if r_score is not None and d_score is not None:
        diff = r_score - d_score
        kill_diff_side = "radiant" if diff > 0 else ("dire" if diff < 0 else "")

    structure_advantage_side = ""
    structure = decode_structure_state(dict(game))
    if structure.confidence >= 0.8:
        radiant_alive = sum(v or 0 for v in (
            structure.radiant_t1_alive,
            structure.radiant_t2_alive,
            structure.radiant_t3_alive,
            structure.radiant_t4_alive,
        ))
        dire_alive = sum(v or 0 for v in (
            structure.dire_t1_alive,
            structure.dire_t2_alive,
            structure.dire_t3_alive,
            structure.dire_t4_alive,
        ))
        if radiant_alive > dire_alive:
            structure_advantage_side = "radiant"
        elif dire_alive > radiant_alive:
            structure_advantage_side = "dire"

    flags: list[str] = []
    if phase_lead is not None and abs(phase_lead) >= 2500:
        flags.append("PHASE_ADJUSTED_NETWORTH_LEAD")
    if lead is not None and abs(lead) >= 8000:
        flags.append("DOMINANT_NETWORTH_LEAD")
    if structure_advantage_side:
        flags.append("STRUCTURE_ADVANTAGE")
    if networth_side and kill_diff_side and networth_side == kill_diff_side:
        flags.append("KILL_NETWORTH_ALIGNMENT")
    if networth_side and kill_diff_side and networth_side != kill_diff_side:
        flags.append("KILL_NETWORTH_DIVERGENCE")
    if structure_advantage_side and networth_side == structure_advantage_side and gt >= 900:
        flags.append("PUSH_SETUP_STATE")

    return DerivedGameState(
        flags=tuple(flags),
        phase_adjusted_networth_lead=phase_lead,
        networth_side=networth_side,
        structure_advantage_side=structure_advantage_side,
        kill_diff_side=kill_diff_side,
    )
