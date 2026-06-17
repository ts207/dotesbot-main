from __future__ import annotations

import os

import yaml

from mapping_validator import MappingError, has_placeholder, validate_active_mappings, validate_mapping_schema
from mapping_quarantine import is_quarantined
from runtime_markets import ALLOWED_RUNTIME_FIELDS, runtime_market_key

DEFAULT_MARKETS_PATH = os.path.join(os.path.dirname(__file__), "markets.yaml")
RUNTIME_MARKETS_PATH = os.path.join(os.path.dirname(__file__), "logs", "runtime_markets.yaml")


def apply_runtime_overlay(base_markets: list[dict]) -> list[dict]:
    """Merge runtime state from logs/runtime_markets.yaml into a copy of base_markets."""
    # Always return a copy to prevent accidental mutation of global seed state
    merged = [dict(m) for m in base_markets]

    if not os.path.exists(RUNTIME_MARKETS_PATH):
        return merged

    try:
        with open(RUNTIME_MARKETS_PATH, "r", encoding="utf-8") as f:
            runtime_data = yaml.safe_load(f)
            if not isinstance(runtime_data, dict):
                return merged
            runtime_markets = runtime_data.get("markets")
            if not isinstance(runtime_markets, list):
                return merged

        # Create lookup by condition_id or yes_token_id
        overlay: dict[str, dict] = {}
        for m in runtime_markets:
            if not isinstance(m, dict):
                continue
            key = runtime_market_key(m)
            if key:
                overlay[key] = m

        seen_keys = set()
        for m in merged:
            key = runtime_market_key(m)
            if not key:
                continue
            seen_keys.add(key)
            if key in overlay:
                over_m = overlay[key]
                for field in ALLOWED_RUNTIME_FIELDS:
                    if field in over_m:
                        m[field] = over_m[field]

        # Also include any markets found ONLY in the runtime file (newly discovered)
        for m in runtime_markets:
            if not isinstance(m, dict):
                continue
            key = runtime_market_key(m)
            if key and key not in seen_keys:
                merged.append(dict(m))

    except Exception as e:
        # Don't let a corrupted runtime file block the whole bot
        print(f"Warning: could not load runtime markets overlay {RUNTIME_MARKETS_PATH}: {e}")
        return merged

    return merged


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
