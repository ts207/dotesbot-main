# Data Leakage Audit

| Risk Area | Classification | Notes |
|---|---|---|
| Settlement fields before prediction | Medium | Needs careful validation that `settled_yes_outcome` is not used in features. |
| Future markout fields in features | Low | Replay generation typically appends these at the end. |
| Post-settlement fields in features | Low | Models usually use `dota_game_time` strictly. |
| Duplicate rows from YES/NO | High | Seen multiple rows for same match_id / timestamp_ns if not deduplicated. |
| Training/Validation overlap | Unknown | Requires model training manifest to verify. |
