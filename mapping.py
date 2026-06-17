from __future__ import annotations

import os

import yaml

from mapping_validator import MappingError, has_placeholder, validate_active_mappings, validate_mapping_schema
from mapping_quarantine import is_quarantined

DEFAULT_MARKETS_PATH = os.path.join(os.path.dirname(__file__), "markets.yaml")
RUNTIME_MARKETS_PATH = os.path.join(os.path.dirname(__file__), "logs", "runtime_markets.yaml")


def apply_runtime_overlay(base_markets: list[dict]) -> list[dict]:
    """Merge runtime state from logs/runtime_markets.yaml into the given markets list."""
    if not os.path.exists(RUNTIME_MARKETS_PATH):
        return base_markets

    try:
        with open(RUNTIME_MARKETS_PATH, "r", encoding="utf-8") as f:
            runtime_data = yaml.safe_load(f) or {}
            runtime_markets = runtime_data.get("markets", []) or []

        # Create lookup by condition_id or yes_token_id
        overlay: dict[str, dict] = {}
        for m in runtime_markets:
            key = m.get("condition_id") or m.get("yes_token_id")
            if key:
                overlay[str(key)] = m

        # Fields that are allowed to be updated by runtime
        overlay_fields = {
            "dota_match_id", "confidence", "auto_mapped_at_utc",
            "auto_mapped_source", "steam_radiant_team", "steam_dire_team",
            "steam_side_mapping", "current_game_number",
            "series_score_yes", "series_score_no", "p_next_yes",
            "quarantined", "quarantine_reason"
        }

        seen_keys = set()
        for m in base_markets:
            key = m.get("condition_id") or m.get("yes_token_id")
            if not key:
                continue
            k_str = str(key)
            seen_keys.add(k_str)
            if k_str in overlay:
                over_m = overlay[k_str]
                for field in overlay_fields:
                    if field in over_m:
                        m[field] = over_m[field]

        # Also include any markets found ONLY in the runtime file (newly discovered)
        for m in runtime_markets:
            key = m.get("condition_id") or m.get("yes_token_id")
            if key and str(key) not in seen_keys:
                base_markets.append(m)

    except Exception as e:
        # Don't let a corrupted runtime file block the whole bot
        print(f"Warning: could not load runtime markets overlay: {e}")

    return base_markets


def load_mappings(filename: str | None = None) -> list[dict]:
    """Load mappings from markets.yaml, merged with runtime overlay from logs/."""
    if filename is None:
        filename = DEFAULT_MARKETS_PATH
    
    base_markets: list[dict] = []
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            base_markets = data.get("markets", []) or []

    # Overlay runtime state if loading the default markets file
    if filename == DEFAULT_MARKETS_PATH:
        return apply_runtime_overlay(base_markets)

    return base_markets


def validate_mapping(mapping: dict, index: int = 0) -> tuple[bool, MappingError | None]:
    result = validate_mapping_schema(mapping, index)
    if result.mapping_errors:
        return False, MappingError(index=index, name=mapping.get("name"), reason="; ".join(result.mapping_errors))

    market_type = str(mapping.get("market_type", "")).upper()
    confidence = float(mapping.get("confidence", 0))
    mapping["market_type"] = market_type
    mapping["confidence"] = confidence
    mapping["yes_token_id"] = str(mapping["yes_token_id"])
    mapping["no_token_id"] = str(mapping["no_token_id"])
    mapping["dota_match_id"] = str(mapping["dota_match_id"])
    return True, None


def load_valid_mappings(filename: str | None = None) -> tuple[list[dict], list[MappingError]]:
    if filename is None:
        filename = DEFAULT_MARKETS_PATH
    raw = load_mappings(filename)
    valid: list[dict] = []
    errors: list[MappingError] = []
    results = validate_active_mappings([dict(m) for m in raw])

    for i, (mapping, result) in enumerate(zip(raw, results)):
        mapping = dict(mapping)  # copy so validate_mapping's normalisation doesn't mutate the original
        if is_quarantined(mapping):
            errors.append(MappingError(index=i, name=mapping.get("name"), reason=f"mapping_quarantined:{mapping.get('quarantine_reason') or 'unknown'}"))
            continue
        if result.mapping_errors:
            errors.append(MappingError(index=i, name=mapping.get("name"), reason="; ".join(result.mapping_errors)))
            continue

        ok, err = validate_mapping(mapping, i)
        if not ok and err:
            errors.append(err)
            continue

        mapping["mapping_confidence"] = result.mapping_confidence
        mapping["mapping_errors"] = ""
        mapping["series_id"] = mapping.get("series_id") or result.series_id
        mapping["series_type"] = mapping.get("series_type") if mapping.get("series_type") is not None else result.series_type
        mapping["game_number"] = mapping.get("game_number") or result.game_number
        mapping["team_id_match"] = result.team_id_match
        mapping["market_game_number_match"] = result.market_game_number_match
        mapping["duplicate_match_id_error"] = result.duplicate_match_id_error
        valid.append(mapping)

    return valid, errors
