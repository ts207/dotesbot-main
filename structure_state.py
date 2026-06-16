from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SIDE_MASK = 0x7FF

T1_MASK = (1 << 0) | (1 << 3) | (1 << 6)
T2_MASK = (1 << 1) | (1 << 4) | (1 << 7)
T3_MASK = (1 << 2) | (1 << 5) | (1 << 8)
T4_MASK = (1 << 9) | (1 << 10)

RAX_MELEE_MASK = (1 << 0) | (1 << 2) | (1 << 4)
RAX_RANGE_MASK = (1 << 1) | (1 << 3) | (1 << 5)
ANCIENT_MASK   = (1 << 9) | (1 << 10)


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _count(bits: int, mask: int) -> int:
    return int((bits & mask).bit_count())


@dataclass(frozen=True)
class StructureState:
    match_id: str
    game_time_sec: int | None
    source_field: str
    schema: str
    raw_value: int | None

    radiant_t1_alive: int | None
    radiant_t2_alive: int | None
    radiant_t3_alive: int | None
    radiant_t4_alive: int | None
    dire_t1_alive: int | None
    dire_t2_alive: int | None
    dire_t3_alive: int | None
    dire_t4_alive: int | None
    confidence: float

    radiant_rax_melee_alive: int | None = None
    radiant_rax_range_alive: int | None = None
    radiant_anc_alive: int | None = None
    dire_rax_melee_alive: int | None = None
    dire_rax_range_alive: int | None = None
    dire_anc_alive: int | None = None
    reason: str = ""

    def total_alive(self) -> int | None:
        vals = [
            self.radiant_t1_alive,
            self.radiant_t2_alive,
            self.radiant_t3_alive,
            self.radiant_t4_alive,
            self.dire_t1_alive,
            self.dire_t2_alive,
            self.dire_t3_alive,
            self.dire_t4_alive,
        ]
        if any(v is None for v in vals):
            return None
        return int(sum(vals))


@dataclass(frozen=True)
class StructureDelta:
    valid: bool
    reason: str
    source_field: str
    schema: str
    confidence: float

    radiant_t2_before: int | None = None
    radiant_t2_after: int | None = None
    radiant_t3_before: int | None = None
    radiant_t3_after: int | None = None
    radiant_t4_before: int | None = None
    radiant_t4_after: int | None = None
    radiant_rax_melee_before: int | None = None
    radiant_rax_melee_after: int | None = None
    radiant_rax_range_before: int | None = None
    radiant_rax_range_after: int | None = None

    dire_t2_before: int | None = None
    dire_t2_after: int | None = None
    dire_t3_before: int | None = None
    dire_t3_after: int | None = None
    dire_t4_before: int | None = None
    dire_t4_after: int | None = None
    dire_rax_melee_before: int | None = None
    dire_rax_melee_after: int | None = None
    dire_rax_range_before: int | None = None
    dire_rax_range_after: int | None = None

    radiant_t2_fallen: int = 0
    radiant_t3_fallen: int = 0
    radiant_t4_fallen: int = 0
    radiant_rax_melee_fallen: int = 0
    radiant_rax_range_fallen: int = 0

    dire_t2_fallen: int = 0
    dire_t3_fallen: int = 0
    dire_t4_fallen: int = 0
    dire_rax_melee_fallen: int = 0
    dire_rax_range_fallen: int = 0


def decode_structure_state(snapshot: dict) -> StructureState:
    match_id = str(snapshot.get("match_id") or snapshot.get("lobby_id") or "")
    game_time_sec = _to_int(snapshot.get("game_time_sec"))

    tower_bits = _to_int(snapshot.get("tower_state"))
    building_bits = _to_int(snapshot.get("building_state"))
    building_schema = str(snapshot.get("building_state_schema") or "")

    if tower_bits is None:
        return StructureState(
            match_id=match_id, game_time_sec=game_time_sec,
            source_field="none", schema="missing", raw_value=None,
            radiant_t1_alive=None, radiant_t2_alive=None, radiant_t3_alive=None, radiant_t4_alive=None,
            dire_t1_alive=None, dire_t2_alive=None, dire_t3_alive=None, dire_t4_alive=None,
            confidence=0.0, reason="missing_tower_state",
        )

    r_towers = tower_bits & SIDE_MASK
    d_towers = (tower_bits >> 11) & SIDE_MASK
    
    r_rax_melee, r_rax_range, r_anc = None, None, None
    d_rax_melee, d_rax_range, d_anc = None, None, None
    
    can_decode_buildings = building_bits is not None and building_schema != "top_live_lane_tower_progress"

    if can_decode_buildings:
        r_build = building_bits & SIDE_MASK
        d_build = (building_bits >> 11) & SIDE_MASK
        r_rax_melee = _count(r_build, RAX_MELEE_MASK)
        r_rax_range = _count(r_build, RAX_RANGE_MASK)
        r_anc       = _count(r_build, ANCIENT_MASK)
        d_rax_melee = _count(d_build, RAX_MELEE_MASK)
        d_rax_range = _count(d_build, RAX_RANGE_MASK)
        d_anc       = _count(d_build, ANCIENT_MASK)

    return StructureState(
        match_id=match_id, game_time_sec=game_time_sec,
        source_field="tower_plus_building" if can_decode_buildings else "tower_state",
        schema="dota_22bit_v2" if can_decode_buildings else "tower_22bit_v1",
        raw_value=tower_bits,
        radiant_t1_alive=_count(r_towers, T1_MASK),
        radiant_t2_alive=_count(r_towers, T2_MASK),
        radiant_t3_alive=_count(r_towers, T3_MASK),
        radiant_t4_alive=_count(r_towers, T4_MASK),
        radiant_rax_melee_alive=r_rax_melee,
        radiant_rax_range_alive=r_rax_range,
        radiant_anc_alive=r_anc,
        dire_t1_alive=_count(d_towers, T1_MASK),
        dire_t2_alive=_count(d_towers, T2_MASK),
        dire_t3_alive=_count(d_towers, T3_MASK),
        dire_t4_alive=_count(d_towers, T4_MASK),
        dire_rax_melee_alive=d_rax_melee,
        dire_rax_range_alive=d_rax_range,
        dire_anc_alive=d_anc,
        confidence=1.0 if can_decode_buildings else 0.85,
        reason="" if can_decode_buildings else (
            "top_live_building_state_not_rax_mask"
            if building_schema == "top_live_lane_tower_progress"
            else "missing_building_state"
        ),
    )


def diff_structure_state(prev: StructureState, cur: StructureState) -> StructureDelta:
    if prev.match_id != cur.match_id:
        return _invalid(cur, "match_id_changed")

    if prev.game_time_sec is not None and cur.game_time_sec is not None:
        if cur.game_time_sec < prev.game_time_sec:
            return _invalid(cur, "game_time_moved_backward")

    if prev.schema != cur.schema:
        # Allow schema upgrade from missing buildings to present buildings
        if not (prev.schema == "tower_22bit_v1" and cur.schema == "dota_22bit_v2"):
             return _invalid(cur, "structure_schema_changed")

    if prev.confidence < 0.8 or cur.confidence < 0.8:
        return _invalid(cur, cur.reason or prev.reason or "low_structure_confidence")

    prev_total = prev.total_alive()
    cur_total = cur.total_alive()
    if prev_total is not None and cur_total is not None and cur_total > prev_total:
        return _invalid(cur, "structure_count_increased")

    radiant_t2_fallen = _fallen(prev.radiant_t2_alive, cur.radiant_t2_alive)
    radiant_t3_fallen = _fallen(prev.radiant_t3_alive, cur.radiant_t3_alive)
    radiant_t4_fallen = _fallen(prev.radiant_t4_alive, cur.radiant_t4_alive)
    radiant_rax_melee_fallen = _fallen(prev.radiant_rax_melee_alive, cur.radiant_rax_melee_alive)
    radiant_rax_range_fallen = _fallen(prev.radiant_rax_range_alive, cur.radiant_rax_range_alive)

    dire_t2_fallen = _fallen(prev.dire_t2_alive, cur.dire_t2_alive)
    dire_t3_fallen = _fallen(prev.dire_t3_alive, cur.dire_t3_alive)
    dire_t4_fallen = _fallen(prev.dire_t4_alive, cur.dire_t4_alive)
    dire_rax_melee_fallen = _fallen(prev.dire_rax_melee_alive, cur.dire_rax_melee_alive)
    dire_rax_range_fallen = _fallen(prev.dire_rax_range_alive, cur.dire_rax_range_alive)

    if radiant_t4_fallen and prev.radiant_t3_alive == 3:
        return _invalid(cur, "radiant_t4_fell_while_all_t3_alive")
    if dire_t4_fallen and prev.dire_t3_alive == 3:
        return _invalid(cur, "dire_t4_fell_while_all_t3_alive")

    if not any([
        radiant_t2_fallen, radiant_t3_fallen, radiant_t4_fallen,
        radiant_rax_melee_fallen, radiant_rax_range_fallen,
        dire_t2_fallen, dire_t3_fallen, dire_t4_fallen,
        dire_rax_melee_fallen, dire_rax_range_fallen,
    ]):
        return _invalid(cur, "no_tower_delta")

    return StructureDelta(
        valid=True,
        reason="ok",
        source_field=cur.source_field,
        schema=cur.schema,
        confidence=min(prev.confidence, cur.confidence),

        radiant_t2_before=prev.radiant_t2_alive,
        radiant_t2_after=cur.radiant_t2_alive,
        radiant_t3_before=prev.radiant_t3_alive,
        radiant_t3_after=cur.radiant_t3_alive,
        radiant_t4_before=prev.radiant_t4_alive,
        radiant_t4_after=cur.radiant_t4_alive,
        radiant_rax_melee_before=prev.radiant_rax_melee_alive,
        radiant_rax_melee_after=cur.radiant_rax_melee_alive,
        radiant_rax_range_before=prev.radiant_rax_range_alive,
        radiant_rax_range_after=cur.radiant_rax_range_alive,

        dire_t2_before=prev.dire_t2_alive,
        dire_t2_after=cur.dire_t2_alive,
        dire_t3_before=prev.dire_t3_alive,
        dire_t3_after=cur.dire_t3_alive,
        dire_t4_before=prev.dire_t4_alive,
        dire_t4_after=cur.dire_t4_alive,
        dire_rax_melee_before=prev.dire_rax_melee_alive,
        dire_rax_melee_after=cur.dire_rax_melee_alive,
        dire_rax_range_before=prev.dire_rax_range_alive,
        dire_rax_range_after=cur.dire_rax_range_alive,

        radiant_t2_fallen=radiant_t2_fallen,
        radiant_t3_fallen=radiant_t3_fallen,
        radiant_t4_fallen=radiant_t4_fallen,
        radiant_rax_melee_fallen=radiant_rax_melee_fallen,
        radiant_rax_range_fallen=radiant_rax_range_fallen,

        dire_t2_fallen=dire_t2_fallen,
        dire_t3_fallen=dire_t3_fallen,
        dire_t4_fallen=dire_t4_fallen,
        dire_rax_melee_fallen=dire_rax_melee_fallen,
        dire_rax_range_fallen=dire_rax_range_fallen,
    )


def _fallen(before: int | None, after: int | None) -> int:
    if before is None or after is None:
        return 0
    return max(0, before - after)


def _invalid(cur: StructureState, reason: str) -> StructureDelta:
    return StructureDelta(
        valid=False,
        reason=reason,
        source_field=cur.source_field,
        schema=cur.schema,
        confidence=cur.confidence,
    )
