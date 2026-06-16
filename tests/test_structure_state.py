from structure_state import decode_structure_state, diff_structure_state


def test_building_state_only_not_decoded():
    s = decode_structure_state({"match_id": "1", "game_time_sec": 10, "building_state": 123})
    assert s.confidence == 0.0
    # Schema sentinel renamed in structure_state.py: "building_unknown" → "missing".
    assert s.schema == "missing"


def test_tower_count_increase_invalid():
    prev = decode_structure_state({"match_id": "1", "game_time_sec": 10, "tower_state": 0})
    cur = decode_structure_state({"match_id": "1", "game_time_sec": 20, "tower_state": 1})
    d = diff_structure_state(prev, cur)
    assert not d.valid
    assert d.reason == "structure_count_increased"


def test_top_live_building_state_schema_does_not_decode_rax():
    tower_state = (1 << 22) - 1
    prev = decode_structure_state({
        "match_id": "1",
        "game_time_sec": 10,
        "building_state": 0x490049,
        "building_state_schema": "top_live_lane_tower_progress",
        "tower_state": tower_state,
    })
    cur = decode_structure_state({
        "match_id": "1",
        "game_time_sec": 20,
        "building_state": 0x490048,
        "building_state_schema": "top_live_lane_tower_progress",
        "tower_state": tower_state,
    })

    assert prev.radiant_rax_melee_alive is None
    assert prev.schema == "tower_22bit_v1"
    assert prev.reason == "top_live_building_state_not_rax_mask"
    d = diff_structure_state(prev, cur)
    assert not d.valid
    assert d.reason == "no_tower_delta"
