from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from mapping_quarantine import quarantine_mapping
from mapping_validator import has_placeholder, validate_active_mappings
from team_utils import teams_match


@dataclass(frozen=True)
class MappingAuditIssue:
    index: int
    name: str
    reason: str
    severity: str = "error"


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _is_active(mapping: dict) -> bool:
    return _to_float(mapping.get("confidence")) == 1.0 and not has_placeholder(mapping.get("dota_match_id"))


def _side_mapping_issue(mapping: dict) -> str | None:
    yes = mapping.get("yes_team")
    no = mapping.get("no_team")
    radiant = mapping.get("steam_radiant_team")
    dire = mapping.get("steam_dire_team")
    if not (yes and no and radiant and dire):
        return None
    side_map = mapping.get("steam_side_mapping")
    normal = teams_match(yes, radiant) and teams_match(no, dire)
    reversed_side = teams_match(yes, dire) and teams_match(no, radiant)
    if side_map == "normal" and not normal:
        return "team_name_mismatch:normal_side_mapping"
    if side_map == "reversed" and not reversed_side:
        return "team_name_mismatch:reversed_side_mapping"
    if side_map not in {"normal", "reversed"} and not (normal or reversed_side):
        return "team_name_mismatch"
    return None


def _orientation_issue(mapping: dict, game: dict | None, book: dict | None) -> str | None:
    if not game or not book:
        return None
    radiant_lead = _to_float(game.get("radiant_lead"))
    yes_ask = _to_float(book.get("best_ask"))
    if radiant_lead is None or yes_ask is None:
        return None
    side_map = mapping.get("steam_side_mapping")
    if side_map == "normal":
        yes_lead = radiant_lead
    elif side_map == "reversed":
        yes_lead = -radiant_lead
    else:
        return None
    if yes_lead > 5000 and yes_ask < 0.35:
        return f"orientation_flip_suspected:yes_lead={yes_lead:.0f}_yes_ask={yes_ask:.2f}"
    if yes_lead < -5000 and yes_ask > 0.65:
        return f"orientation_flip_suspected:yes_lead={yes_lead:.0f}_yes_ask={yes_ask:.2f}"
    return None


def audit_mappings(
    mappings: Iterable[dict],
    *,
    games_by_match_id: dict[str, dict] | None = None,
    books_by_yes_token: dict[str, dict] | None = None,
    now: datetime | None = None,
) -> list[MappingAuditIssue]:
    mappings = list(mappings)
    now = now or datetime.now(timezone.utc)
    issues: list[MappingAuditIssue] = []

    validation_results = validate_active_mappings([dict(m) for m in mappings])
    for i, (mapping, result) in enumerate(zip(mappings, validation_results)):
        name = str(mapping.get("name") or f"#{i}")
        for err in result.mapping_errors:
            issues.append(MappingAuditIssue(i, name, err))

        if mapping.get("yes_token_id") and mapping.get("no_token_id") and mapping.get("yes_token_id") == mapping.get("no_token_id"):
            issues.append(MappingAuditIssue(i, name, "duplicate yes/no token"))

        if _is_active(mapping):
            dt = _to_dt(mapping.get("scheduled_start_utc"))
            if dt is not None and abs((now - dt).total_seconds()) > 7 * 86400:
                issues.append(MappingAuditIssue(i, name, "scheduled_start_utc stale", "warning"))
            side_issue = _side_mapping_issue(mapping)
            if side_issue:
                issues.append(MappingAuditIssue(i, name, side_issue))

            match_id = str(mapping.get("dota_match_id") or "")
            game = (games_by_match_id or {}).get(match_id)
            book = (books_by_yes_token or {}).get(str(mapping.get("yes_token_id") or ""))
            orientation = _orientation_issue(mapping, game, book)
            if orientation:
                issues.append(MappingAuditIssue(i, name, orientation, "critical"))

            if str(mapping.get("market_type") or "").upper() == "MATCH_WINNER":
                current_game = mapping.get("current_game_number") or mapping.get("game_number")
                try:
                    current_game_int = int(current_game)
                except (TypeError, ValueError):
                    current_game_int = None
                if current_game_int is not None and current_game_int != 3 and mapping.get("treat_as_map_winner"):
                    issues.append(MappingAuditIssue(i, name, "MATCH_WINNER non-decider incorrectly treated as MAP_WINNER"))

    return issues


def quarantine_critical_issues(mappings: list[dict], issues: Iterable[MappingAuditIssue]) -> int:
    count = 0
    for issue in issues:
        if issue.severity == "critical":
            quarantine_mapping(mappings[issue.index], issue.reason)
            count += 1
    return count


def load_markets(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {"markets": []}
    return yaml.safe_load(p.read_text()) or {"markets": []}


def write_markets(path: str | Path, data: dict) -> None:
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False))
    tmp.replace(p)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit markets.yaml mapping safety")
    parser.add_argument("--markets", default="markets.yaml")
    parser.add_argument("--quarantine-critical", action="store_true")
    args = parser.parse_args()

    data = load_markets(args.markets)
    markets = data.get("markets", []) if isinstance(data, dict) else []
    issues = audit_mappings(markets)
    for issue in issues:
        print(f"{issue.severity}\t#{issue.index}\t{issue.name}\t{issue.reason}")
    if args.quarantine_critical:
        changed = quarantine_critical_issues(markets, issues)
        if changed:
            write_markets(args.markets, data)
        print(f"quarantined={changed}")
    return 2 if any(issue.severity == "critical" for issue in issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
