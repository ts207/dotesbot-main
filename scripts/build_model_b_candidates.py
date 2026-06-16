#!/usr/bin/env python3
"""Build Model B candidate rows from market, proxy, mapping, Dota, and trait data."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from statistics import mean


HEADERS = [
    "market_id",
    "condition_id",
    "event_id",
    "yes_token_id",
    "no_token_id",
    "slug",
    "question",
    "source_universe",
    "market_discovery_source",
    "discovery_query",
    "game_id",
    "match_id",
    "series_id",
    "dataset_role",
    "is_locked_execution_audit",
    "start_ts",
    "patch_epoch",
    "team_a_id",
    "team_b_id",
    "team_a_is_radiant",
    "team_a_win",
    "decision_ts",
    "decision_ts_source",
    "p_market_early_mid",
    "market_probability_source",
    "proxy_ts",
    "timestamp_confidence",
    "asof_valid",
    "mapping_confidence",
    "team_a_hero_ids_json",
    "team_b_hero_ids_json",
    "team_a_inferred_roles_json",
    "team_b_inferred_roles_json",
    "role_inference_confidence",
    "role_inference_method",
    "scaling_diff",
    "tempo_diff",
    "lane_diff",
    "fight_diff",
    "tower_diff",
    "volatility_diff",
    "a_tempo_b_scaling",
    "a_scaling_b_tempo",
    "a_tower_b_fight",
    "a_fight_b_fight",
    "trait_coverage",
    "fallback_share",
    "market_volume",
    "market_liquidity",
    "tournament_tier",
    "execution_price_available",
    "analysis_ready_probability",
    "analysis_ready_execution",
    "diagnostic_only",
    "blocker_reason",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def fnum(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_locked(path: Path) -> set[str]:
    return {r.get("market_id", "") for r in read_csv(path) if r.get("market_id")}


def load_locked_summary(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_traits(path: Path) -> tuple[dict[tuple[str, str], dict], dict[str, dict]]:
    exact = {}
    fallback = {}
    for row in read_csv(path):
        hero = str(row.get("hero_id") or "")
        role = str(int(float(row.get("role") or 0)))
        if row.get("trait_scope") == "global_hero":
            fallback[hero] = row
        else:
            exact[(hero, role)] = row
    return exact, fallback


def hero_sides(match: dict, team_a_is_radiant: str) -> tuple[list[dict], list[dict]]:
    players = match.get("players") or []
    radiant = [p for p in players if p.get("player_slot") is not None and int(p.get("player_slot")) < 128]
    dire = [p for p in players if p.get("player_slot") is not None and int(p.get("player_slot")) >= 128]
    return (radiant, dire) if team_a_is_radiant == "1" else (dire, radiant)


def trait_values(players: list[dict], exact: dict, fallback: dict) -> tuple[dict[str, float], float, float, list[int], list[int]]:
    totals = {"scaling": [], "tempo": [], "tower": [], "fight": [], "volatility": []}
    exact_count = 0
    fallback_count = 0
    hero_ids = []
    roles = []
    for p in players:
        hero = str(p.get("hero_id") or "")
        role = str(int(p.get("lane_role"))) if p.get("lane_role") is not None else "0"
        if not hero:
            continue
        hero_ids.append(int(hero))
        roles.append(int(role))
        row = exact.get((hero, role))
        if row:
            exact_count += 1
        else:
            row = fallback.get(hero)
            if row:
                fallback_count += 1
        if not row:
            continue
        totals["scaling"].append(float(row.get("scaling_score") or 0))
        totals["tempo"].append(float(row.get("tempo_score") or 0))
        totals["tower"].append(float(row.get("tower_score") or 0))
        totals["fight"].append(float(row.get("fight_score") or 0))
        totals["volatility"].append(float(row.get("volatility_score") or 0))
    covered = exact_count + fallback_count
    total = len(hero_ids) or 1
    values = {k: mean(v) if v else 0.0 for k, v in totals.items()}
    return values, covered / total, fallback_count / total, hero_ids, roles


def team_a_is_radiant(mapping: dict[str, str], clean: dict[str, str], match: dict | None) -> str:
    if clean.get("team_a_is_radiant") in {"0", "1"}:
        return clean["team_a_is_radiant"]
    if mapping.get("team_a_is_radiant") in {"0", "1"}:
        return mapping["team_a_is_radiant"]
    if not match:
        return ""
    yes_team = mapping.get("yes_team_id", "")
    if yes_team and yes_team == str(match.get("radiant_team_id") or ""):
        return "1"
    if yes_team and yes_team == str(match.get("dire_team_id") or ""):
        return "0"
    return ""


def outcome(mapping: dict[str, str], clean: dict[str, str], match: dict | None, team_a_rad: str) -> str:
    if clean.get("team_a_win") in {"0", "1"}:
        return clean["team_a_win"]
    if mapping.get("team_a_win") in {"0", "1"}:
        return mapping["team_a_win"]
    if not match or team_a_rad not in {"0", "1"}:
        return ""
    radiant_win = match.get("radiant_win")
    if radiant_win is None:
        return ""
    return str(int(bool(radiant_win) if team_a_rad == "1" else not bool(radiant_win)))


CLUB_OR_STREAMER_PATTERNS = [
    "betboom streamers battle",
    "team 9pasha",
    "team ns",
    "team rostikfacekid",
    "team tpabomah",
    "team voodoosh",
    "team vovapain",
    "travoman team",
    "miposhka team",
    "by owl team",
    "cooman team",
    "nix team",
    "soloteam",
    "stray team",
]


def is_club_or_streamer_market(mapping: dict[str, str]) -> bool:
    text = " ".join(
        [
            mapping.get("question", ""),
            mapping.get("slug", ""),
            mapping.get("discovery_query", ""),
            mapping.get("market_discovery_source", ""),
        ]
    ).lower()
    return any(pattern in text for pattern in CLUB_OR_STREAMER_PATTERNS)


def build_row(mapping, clean, proxy, match, exact_traits, fallback_traits, locked_ids):
    blockers = []
    market_id = mapping.get("market_id", "")
    is_locked = market_id in locked_ids
    mapping_conf = fnum(mapping.get("mapping_confidence")) if mapping else None
    p_mid = fnum(proxy.get("p_market_early_mid")) if proxy else None
    ts_conf = fnum(proxy.get("timestamp_confidence")) if proxy else None
    team_a_rad = team_a_is_radiant(mapping, clean, match)
    team_a_win = outcome(mapping, clean, match, team_a_rad)
    if is_locked:
        blockers.append("locked_execution_audit")
    if is_club_or_streamer_market(mapping):
        blockers.append("club_or_streamer_match")
    if mapping_conf is None or mapping_conf < 0.95:
        blockers.append("not_mapped")
    if team_a_win == "":
        blockers.append("missing_outcome")
    if p_mid is None or not (0.02 <= p_mid <= 0.98):
        blockers.append("missing_price_history" if (proxy and proxy.get("proxy_notes", "").startswith("missing_price_history")) else "missing_or_invalid_market_probability_proxy")
    if not proxy or proxy.get("asof_valid") != "True":
        blockers.append("asof_invalid")
    if ts_conf is None or ts_conf < 0.70:
        blockers.append("timestamp_confidence_low")
    if not match or len(match.get("players") or []) != 10:
        blockers.append("missing_dota_details" if mapping.get("match_id") else "not_mapped")
        blockers.append("missing_draft")
    a_players, b_players = hero_sides(match or {}, team_a_rad)
    a_vals, a_cov, a_fb, a_heroes, a_roles = trait_values(a_players, exact_traits, fallback_traits)
    b_vals, b_cov, b_fb, b_heroes, b_roles = trait_values(b_players, exact_traits, fallback_traits)
    trait_coverage = (a_cov + b_cov) / 2.0
    fallback_share = (a_fb + b_fb) / 2.0
    if trait_coverage < 0.80:
        blockers.append("low_trait_coverage")
    ready_probability = not blockers
    ready_execution = ready_probability and proxy and proxy.get("execution_price_available") == "True"
    dataset_role = "locked_execution_audit" if is_locked else "train_pool_candidate" if ready_probability else "diagnostic_only"
    diffs = {k: a_vals[k] - b_vals[k] for k in a_vals}
    return {
        "market_id": market_id,
        "condition_id": mapping.get("condition_id", ""),
        "event_id": mapping.get("event_id", ""),
        "yes_token_id": mapping.get("yes_token_id", ""),
        "no_token_id": mapping.get("no_token_id", ""),
        "slug": mapping.get("slug", ""),
        "question": mapping.get("question", ""),
        "source_universe": mapping.get("source_universe", "local_clean_v2"),
        "market_discovery_source": mapping.get("market_discovery_source", ""),
        "discovery_query": mapping.get("discovery_query", ""),
        "game_id": mapping.get("game_id", market_id),
        "match_id": mapping.get("match_id", ""),
        "series_id": mapping.get("series_id", mapping.get("match_id", "")),
        "dataset_role": dataset_role,
        "is_locked_execution_audit": str(is_locked),
        "start_ts": (match or {}).get("start_time", ""),
        "patch_epoch": (match or {}).get("patch", ""),
        "team_a_id": mapping.get("yes_team_id", "") or clean.get("team_a_id", ""),
        "team_b_id": mapping.get("no_team_id", "") or clean.get("team_b_id", ""),
        "team_a_is_radiant": team_a_rad,
        "team_a_win": team_a_win,
        "decision_ts": proxy.get("decision_ts", "") if proxy else "",
        "decision_ts_source": proxy.get("decision_ts_source", "") if proxy else "",
        "p_market_early_mid": proxy.get("p_market_early_mid", "") if proxy else "",
        "market_probability_source": proxy.get("proxy_source", "") if proxy else "",
        "proxy_ts": proxy.get("proxy_ts", "") if proxy else "",
        "timestamp_confidence": proxy.get("timestamp_confidence", "") if proxy else "",
        "asof_valid": proxy.get("asof_valid", "False") if proxy else "False",
        "mapping_confidence": mapping.get("mapping_confidence", ""),
        "team_a_hero_ids_json": json.dumps(a_heroes, separators=(",", ":")),
        "team_b_hero_ids_json": json.dumps(b_heroes, separators=(",", ":")),
        "team_a_inferred_roles_json": json.dumps(a_roles, separators=(",", ":")),
        "team_b_inferred_roles_json": json.dumps(b_roles, separators=(",", ":")),
        "role_inference_confidence": "0.50",
        "role_inference_method": "opendota_lane_role_not_position",
        "scaling_diff": f"{diffs['scaling']:.6f}",
        "tempo_diff": f"{diffs['tempo']:.6f}",
        "lane_diff": "0.000000",
        "fight_diff": f"{diffs['fight']:.6f}",
        "tower_diff": f"{diffs['tower']:.6f}",
        "volatility_diff": f"{diffs['volatility']:.6f}",
        "a_tempo_b_scaling": f"{a_vals['tempo'] * b_vals['scaling']:.6f}",
        "a_scaling_b_tempo": f"{a_vals['scaling'] * b_vals['tempo']:.6f}",
        "a_tower_b_fight": f"{a_vals['tower'] * b_vals['fight']:.6f}",
        "a_fight_b_fight": f"{a_vals['fight'] * b_vals['fight']:.6f}",
        "trait_coverage": f"{trait_coverage:.6f}",
        "fallback_share": f"{fallback_share:.6f}",
        "market_volume": mapping.get("market_volume", ""),
        "market_liquidity": mapping.get("market_liquidity", ""),
        "tournament_tier": "",
        "execution_price_available": proxy.get("execution_price_available", "False") if proxy else "False",
        "analysis_ready_probability": str(ready_probability),
        "analysis_ready_execution": str(bool(ready_execution)),
        "diagnostic_only": str(dataset_role == "diagnostic_only"),
        "blocker_reason": ",".join(dict.fromkeys(blockers)),
    }


def count_by(rows: list[dict[str, str]], predicate) -> dict[str, int]:
    return dict(Counter(r.get("source_universe") or "unknown" for r in rows if predicate(r)))


def count_by_query(rows: list[dict[str, str]], predicate) -> dict[str, int]:
    return dict(Counter((r.get("discovery_query") or r.get("market_discovery_source") or "unknown") for r in rows if predicate(r)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean-v2", default="data/clean_v2/matches.csv")
    parser.add_argument("--market-map", default="data/processed/market_game_map.csv")
    parser.add_argument("--proxies", default="data/processed/market_probability_proxies.csv")
    parser.add_argument("--details", default="logs/opendota_player_match_details.json")
    parser.add_argument("--traits", default="data/hero_role_traits.csv")
    parser.add_argument("--locked", default="data/locked_execution_audit/locked_market_ids.csv")
    parser.add_argument("--locked-summary", default="reports/locked_set_summary.json")
    parser.add_argument("--output", default="data/train_pool/model_b_candidates.csv")
    parser.add_argument("--summary-output", default="data/train_pool/summary.json")
    args = parser.parse_args()
    clean_by_market = {r["market_id"]: r for r in read_csv(Path(args.clean_v2)) if r.get("market_id")}
    mappings = [r for r in read_csv(Path(args.market_map)) if r.get("market_id")]
    proxies = {r["market_id"]: r for r in read_csv(Path(args.proxies)) if r.get("market_id")}
    details = load_json(Path(args.details))
    exact_traits, fallback_traits = load_traits(Path(args.traits))
    locked_ids = load_locked(Path(args.locked))
    locked_summary = load_locked_summary(Path(args.locked_summary))
    rows = [
        build_row(
            mapping,
            clean_by_market.get(mapping.get("market_id", ""), {}),
            proxies.get(mapping.get("market_id", "")),
            details.get(mapping.get("match_id", "")),
            exact_traits,
            fallback_traits,
            locked_ids,
        )
        for mapping in mappings
    ]
    write_csv(Path(args.output), rows, HEADERS)
    summary = {
        "locked_execution_audit_expected": locked_summary.get("locked_execution_audit_expected", 103),
        "locked_execution_audit_materialized": locked_summary.get("locked_execution_audit_materialized", len(locked_ids)),
        "locked_missing_or_unresolved": locked_summary.get("locked_missing_or_unresolved", max(103 - len(locked_ids), 0)),
        "locked_execution_audit_rows": sum(r["dataset_role"] == "locked_execution_audit" for r in rows),
        "total_discovered_markets": len(rows),
        "non_locked_total_rows": sum(r["dataset_role"] != "locked_execution_audit" for r in rows),
        "non_locked_probability_ready": sum(r["dataset_role"] == "train_pool_candidate" for r in rows),
        "non_locked_execution_ready": sum(r["analysis_ready_execution"] == "True" and r["dataset_role"] != "locked_execution_audit" for r in rows),
        "non_locked_diagnostic_only": sum(r["dataset_role"] == "diagnostic_only" for r in rows),
        "proxy_source_counts": dict(Counter(r["market_probability_source"] or "missing" for r in rows)),
        "rows_by_source_universe": dict(Counter(r.get("source_universe") or "unknown" for r in rows)),
        "probability_ready_by_source_universe": count_by(rows, lambda r: r["dataset_role"] == "train_pool_candidate"),
        "mapped_by_source_universe": count_by(rows, lambda r: (fnum(r.get("mapping_confidence")) or 0) >= 0.95),
        "probability_ready_by_query": count_by_query(rows, lambda r: r["dataset_role"] == "train_pool_candidate"),
        "mapped_by_query": count_by_query(rows, lambda r: (fnum(r.get("mapping_confidence")) or 0) >= 0.95),
        "missing_price_history_by_source_universe": count_by(rows, lambda r: not r.get("market_probability_source")),
        "blocker_reasons": dict(Counter(reason for r in rows for reason in (r["blocker_reason"].split(",") if r["blocker_reason"] else []))),
        "bottleneck_split": dict(
            Counter(
                reason
                for r in rows
                if r["dataset_role"] == "diagnostic_only"
                for reason in (r["blocker_reason"].split(",") if r["blocker_reason"] else [])
            )
        ),
    }
    Path(args.summary_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary_output).write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"wrote {args.output}")
    print(f"wrote {args.summary_output}")


if __name__ == "__main__":
    main()
