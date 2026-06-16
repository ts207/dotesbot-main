#!/usr/bin/env python3
"""Validate manual team aliases before accepting new market mappings."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


RISKY_SHORT_ALIASES = {"gg", "ar", "vp", "lgd", "ts", "sr"}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def norm(text: str | None) -> str:
    text = (text or "").casefold()
    text = re.sub(r"\b(esports|gaming|team|club|clan)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def canon_key(row: dict[str, str]) -> str:
    return norm(row.get("canonical_team_name") or row.get("canonical_team_id") or row.get("alias"))


def dota_universe_names(rows: list[dict[str, str]]) -> set[str]:
    names = set()
    for row in rows:
        for col in ["radiant_team_name", "dire_team_name"]:
            n = norm(row.get(col))
            if n:
                names.add(n)
    return names


def candidate_mapping_issues(rows: list[dict[str, str]]) -> dict[str, Any]:
    accepted = [r for r in rows if r.get("mapping_decision") == "accepted"]
    result_mismatches = [
        r for r in accepted
        if "result_mismatch" in (r.get("mapping_reject_reason") or "")
        or (r.get("result_match_score") not in {"", "1", "1.0", "1.0000"})
    ]
    accepted_by_market: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in accepted:
        accepted_by_market[row.get("market_id", "")].append(row)
    multiple_accepted = {
        market_id: [r.get("match_id", "") for r in grouped]
        for market_id, grouped in accepted_by_market.items()
        if market_id and len({r.get("match_id", "") for r in grouped}) > 1
    }
    return {
        "accepted_rows": len(accepted),
        "accepted_result_mismatch_rows": len(result_mismatches),
        "markets_with_multiple_accepted_matches": len(multiple_accepted),
        "multiple_accepted_examples": dict(list(multiple_accepted.items())[:10]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aliases", default="data/manual/team_aliases.csv")
    parser.add_argument("--dota-game-universe", default="data/processed/dota_game_universe.csv")
    parser.add_argument("--mapping-candidates", default="reports/network_mapping_candidates.csv")
    parser.add_argument("--output", default="reports/team_alias_validation.json")
    args = parser.parse_args()

    aliases = read_csv(Path(args.aliases))
    dota_names = dota_universe_names(read_csv(Path(args.dota_game_universe)))
    mapping_candidates = read_csv(Path(args.mapping_candidates))

    alias_to_canon: dict[str, set[str]] = defaultdict(set)
    duplicate_rows = 0
    seen_rows: set[tuple[str, str]] = set()
    ambiguous_short = []
    canonical_missing = []
    for row in aliases:
        alias = norm(row.get("alias"))
        canonical = canon_key(row)
        if not alias or not canonical:
            continue
        key = (alias, canonical)
        if key in seen_rows:
            duplicate_rows += 1
        seen_rows.add(key)
        alias_to_canon[alias].add(canonical)

        notes = (row.get("notes") or "").casefold()
        confidence = float(row.get("confidence") or 0)
        raw_alias = (row.get("alias") or "").strip().casefold()
        if raw_alias in RISKY_SHORT_ALIASES and "ambiguous" not in notes and "context" not in notes:
            ambiguous_short.append(row)
        if len(alias) <= 2 and confidence < 1.0 and "ambiguous" not in notes and "context" not in notes:
            ambiguous_short.append(row)
        if canonical not in dota_names:
            canonical_missing.append(row)

    multi_canon = {
        alias: sorted(canonicals)
        for alias, canonicals in alias_to_canon.items()
        if len(canonicals) > 1
    }
    mapping_issues = candidate_mapping_issues(mapping_candidates)

    hard_failures = []
    if multi_canon:
        hard_failures.append("alias_maps_to_multiple_canonical_teams")
    if mapping_issues["accepted_result_mismatch_rows"] > 0:
        hard_failures.append("accepted_mapping_result_mismatch")
    if mapping_issues["markets_with_multiple_accepted_matches"] > 0:
        hard_failures.append("market_has_multiple_accepted_matches")

    report = {
        "status": "fail" if hard_failures else "pass",
        "hard_failures": hard_failures,
        "alias_rows": len(aliases),
        "duplicate_alias_rows": duplicate_rows,
        "aliases_mapping_to_multiple_canonical_teams": multi_canon,
        "ambiguous_short_alias_warnings": [
            {
                "alias": row.get("alias", ""),
                "canonical_team_name": row.get("canonical_team_name", ""),
                "confidence": row.get("confidence", ""),
                "notes": row.get("notes", ""),
            }
            for row in ambiguous_short
        ],
        "canonical_team_not_present_in_dota_universe_warnings": [
            {
                "alias": row.get("alias", ""),
                "canonical_team_name": row.get("canonical_team_name", ""),
                "confidence": row.get("confidence", ""),
                "notes": row.get("notes", ""),
            }
            for row in canonical_missing[:100]
        ],
        "canonical_team_not_present_in_dota_universe_count": len(canonical_missing),
        "mapping_candidate_checks": mapping_issues,
        "hard_reject_rules": [
            "one alias maps to multiple canonical teams without context rule",
            "accepted mapping creates result mismatch",
            "accepted mapping creates non-unique candidate",
        ],
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "alias_rows": report["alias_rows"],
        "hard_failures": report["hard_failures"],
        "ambiguous_short_alias_warnings": len(report["ambiguous_short_alias_warnings"]),
        "canonical_missing_warnings": report["canonical_team_not_present_in_dota_universe_count"],
        "output": str(output),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
