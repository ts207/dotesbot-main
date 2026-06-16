#!/usr/bin/env python3
"""Audit network Polymarket Dota markets that did not map to Dota games."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path


AUDIT_HEADERS = [
    "market_id",
    "condition_id",
    "event_id",
    "source_universe",
    "question",
    "slug",
    "market_team_a_raw",
    "market_team_b_raw",
    "market_team_a_norm",
    "market_team_b_norm",
    "parsed_game_number",
    "market_scope",
    "tournament_hint",
    "market_start_ts",
    "market_end_ts",
    "closed_ts",
    "resolved_outcome",
    "market_date_source",
    "market_date_confidence",
    "search_window_start",
    "search_window_end",
    "candidate_count_before_team_filter",
    "candidate_count_after_team_filter",
    "candidate_count_after_date_filter",
    "candidate_count_after_tournament_filter",
    "candidate_count_pm3d",
    "candidate_count_pm7d",
    "candidate_count_pm14d",
    "candidate_count_any_date_same_teams",
    "reject_stage",
    "reject_reason",
]


ALIAS_GAP_HEADERS = [
    "raw_team_name",
    "normalized_team_name",
    "market_count",
    "example_market_ids",
    "example_questions",
    "source_universe",
    "suggested_alias",
    "candidate_opendota_team_names",
    "candidate_stratz_team_names",
]


MANUAL_QUEUE_HEADERS = [
    "market_id",
    "condition_id",
    "event_id",
    "question",
    "slug",
    "market_team_a",
    "market_team_b",
    "parsed_game_number",
    "tournament_hint",
    "market_date",
    "resolved_outcome",
    "candidate_opendota_matches_pm14d",
    "candidate_stratz_matches_pm14d",
    "suggested_match_id",
    "manual_status",
    "manual_notes",
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


def norm(text: str | None) -> str:
    text = (text or "").casefold()
    text = re.sub(r"\b(esports|gaming|team|club|clan)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        try:
            return datetime.fromtimestamp(float(text), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return None


def load_aliases(path: Path) -> dict[str, str]:
    aliases = {}
    for row in read_csv(path):
        alias = norm(row.get("alias"))
        canonical = norm(row.get("canonical_team_name") or row.get("alias"))
        if alias and canonical:
            aliases[alias] = canonical
    return aliases


def canonical(name: str, aliases: dict[str, str]) -> str:
    n = norm(name)
    return aliases.get(n, n)


def name_score(a: str, b: str, aliases: dict[str, str]) -> float:
    ca, cb = canonical(a, aliases), canonical(b, aliases)
    if not ca or not cb:
        return 0.0
    if ca == cb:
        return 1.0
    if ca in cb or cb in ca:
        return 0.93
    return SequenceMatcher(None, ca, cb).ratio()


def market_date(row: dict[str, str]) -> tuple[datetime | None, str, str]:
    hierarchy = [
        ("event_date_hint", row.get("event_date_hint"), "0.95"),
        ("end_ts", row.get("end_ts"), "0.85"),
        ("start_ts", row.get("start_ts"), "0.75"),
        ("closed_ts", row.get("closed_ts"), "0.50"),
    ]
    for source, value, confidence in hierarchy:
        dt = parse_ts(value)
        if dt:
            return dt, source, confidence
    return None, "", "0.00"


def game_date(row: dict[str, str]) -> datetime | None:
    return parse_ts(row.get("start_ts"))


def in_window(game: dict[str, str], center: datetime | None, days: int) -> bool:
    dt = game_date(game)
    if not dt or not center:
        return False
    return abs((dt - center).total_seconds()) <= days * 86400


def team_match(market: dict[str, str], game: dict[str, str], aliases: dict[str, str], threshold: float = 0.86) -> bool:
    a = market.get("market_team_a_raw") or market.get("candidate_team_a") or ""
    b = market.get("market_team_b_raw") or market.get("candidate_team_b") or ""
    rad = game.get("radiant_team_name", "")
    dire = game.get("dire_team_name", "")
    orient_a = (name_score(a, rad, aliases) + name_score(b, dire, aliases)) / 2.0
    orient_b = (name_score(a, dire, aliases) + name_score(b, rad, aliases)) / 2.0
    return max(orient_a, orient_b) >= threshold


def exact_canonical_team_match(market: dict[str, str], game: dict[str, str], aliases: dict[str, str]) -> bool:
    market_teams = {
        canonical(market.get("market_team_a_raw") or market.get("candidate_team_a") or "", aliases),
        canonical(market.get("market_team_b_raw") or market.get("candidate_team_b") or "", aliases),
    }
    game_teams = {
        canonical(game.get("radiant_team_name", ""), aliases),
        canonical(game.get("dire_team_name", ""), aliases),
    }
    return bool("" not in market_teams and market_teams == game_teams)


def tournament_match(market: dict[str, str], game: dict[str, str], aliases: dict[str, str]) -> bool:
    hint = market.get("tournament_hint", "")
    tournament = game.get("tournament_name", "")
    if not hint or not tournament:
        return True
    return name_score(hint, tournament, aliases) >= 0.50


def best_team_score(team: str, games: list[dict[str, str]], aliases: dict[str, str]) -> float:
    best = 0.0
    for game in games:
        best = max(
            best,
            name_score(team, game.get("radiant_team_name", ""), aliases),
            name_score(team, game.get("dire_team_name", ""), aliases),
        )
    return best


def candidate_team_names(team: str, games: list[dict[str, str]], aliases: dict[str, str], limit: int = 5) -> str:
    scores: dict[str, float] = {}
    for game in games:
        for name in (game.get("radiant_team_name", ""), game.get("dire_team_name", "")):
            if not name:
                continue
            scores[name] = max(scores.get(name, 0.0), name_score(team, name, aliases))
    best = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]
    return ";".join(f"{name}:{score:.2f}" for name, score in best if score >= 0.50)


def candidate_match_text(games: list[dict[str, str]], limit: int = 8) -> str:
    rows = []
    for game in games[:limit]:
        rows.append(
            "|".join(
                [
                    game.get("match_id", ""),
                    game.get("start_ts", ""),
                    game.get("radiant_team_name", ""),
                    game.get("dire_team_name", ""),
                    game.get("winner_team_id", ""),
                    game.get("tournament_name", ""),
                ]
            )
        )
    return ";".join(rows)


def reject_stage(
    market: dict[str, str],
    center: datetime | None,
    games_pm14: list[dict[str, str]],
    team_pm14: list[dict[str, str]],
    aliases: dict[str, str],
) -> tuple[str, str]:
    if market.get("market_scope") != "game_winner":
        return ("series_scope_market" if market.get("market_scope") == "series_winner" else "game_scope_mismatch", market.get("market_scope", ""))
    if not (market.get("market_team_a_raw") and market.get("market_team_b_raw")):
        return "bad_market_parse", "team_names_unparsed"
    if center is None:
        return "missing_market_date", "no usable event/end/start/closed timestamp"
    if not games_pm14:
        return "dota_universe_gap", "no Dota games in +/-14d date window"
    if not team_pm14:
        a_best = best_team_score(market.get("market_team_a_raw", ""), games_pm14, aliases)
        b_best = best_team_score(market.get("market_team_b_raw", ""), games_pm14, aliases)
        if max(a_best, b_best) >= 0.70:
            return "team_alias_missing", f"best_team_scores={a_best:.2f}/{b_best:.2f}"
        return "team_not_found_in_dota_universe", f"best_team_scores={a_best:.2f}/{b_best:.2f}"
    return "date_window_miss", "teams found only outside strict acceptance conditions"


def summarize_ranges(markets: list[dict[str, str]], games: list[dict[str, str]]) -> dict[str, str]:
    market_dates = [market_date(m)[0] for m in markets]
    market_dates = [d for d in market_dates if d]
    game_dates = [game_date(g) for g in games]
    game_dates = [d for d in game_dates if d]
    return {
        "market_min_ts": min(market_dates).isoformat() if market_dates else "",
        "market_max_ts": max(market_dates).isoformat() if market_dates else "",
        "dota_universe_min_ts": min(game_dates).isoformat() if game_dates else "",
        "dota_universe_max_ts": max(game_dates).isoformat() if game_dates else "",
    }


def outside_range_count(markets: list[dict[str, str]], games: list[dict[str, str]]) -> int:
    game_dates = [game_date(g) for g in games]
    game_dates = [d for d in game_dates if d]
    if not game_dates:
        return 0
    min_game = min(game_dates)
    max_game = max(game_dates)
    count = 0
    for market in markets:
        dt, _source, _confidence = market_date(market)
        if dt and (dt < min_game or dt > max_game):
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-universe", default="data/processed/polymarket/dota_market_universe.csv")
    parser.add_argument("--market-map", default="data/processed/market_game_map.csv")
    parser.add_argument("--dota-game-universe", default="data/processed/dota_game_universe.csv")
    parser.add_argument("--team-aliases", default="data/manual/team_aliases.csv")
    parser.add_argument("--audit-output", default="reports/no_dota_candidate_audit.csv")
    parser.add_argument("--summary-output", default="reports/no_dota_candidate_summary.json")
    parser.add_argument("--alias-gaps-output", default="reports/team_alias_gaps.csv")
    parser.add_argument("--manual-queue-output", default="reports/manual_mapping_queue.csv")
    parser.add_argument("--coverage-output", default="reports/dota_universe_coverage_audit.json")
    parser.add_argument("--include-all-unmapped", action="store_true")
    args = parser.parse_args()

    markets = {r["market_id"]: r for r in read_csv(Path(args.market_universe)) if r.get("market_id")}
    mappings = {r["market_id"]: r for r in read_csv(Path(args.market_map)) if r.get("market_id")}
    games = read_csv(Path(args.dota_game_universe))
    aliases = load_aliases(Path(args.team_aliases))
    known_alias_norms = set(aliases.keys())
    audit_rows = []
    manual_rows = []
    alias_examples: dict[str, dict[str, object]] = {}
    target_markets = []

    for market_id, market in markets.items():
        if market.get("source_universe") == "local_clean_v2":
            continue
        mapping = mappings.get(market_id, {})
        if float(mapping.get("mapping_confidence") or 0) >= 0.95:
            continue
        if not args.include_all_unmapped and mapping.get("mapping_notes") != "no_dota_candidate":
            continue
        target_markets.append(market)
        center, date_source, date_conf = market_date(market)
        pm3 = [g for g in games if in_window(g, center, 3)]
        pm7 = [g for g in games if in_window(g, center, 7)]
        pm14 = [g for g in games if in_window(g, center, 14)]
        team7 = [g for g in pm7 if team_match(market, g, aliases)]
        team14 = [g for g in pm14 if team_match(market, g, aliases)]
        any_date_same_teams = [g for g in games if exact_canonical_team_match(market, g, aliases)]
        date_filtered = [g for g in team7 if in_window(g, center, 3)]
        tournament_filtered = [g for g in date_filtered if tournament_match(market, g, aliases)]
        stage, reason = reject_stage(market, center, pm14, team14, aliases)

        for raw in (market.get("market_team_a_raw", ""), market.get("market_team_b_raw", "")):
            if not raw:
                continue
            key = norm(raw)
            if key in known_alias_norms or canonical(raw, aliases) != key:
                continue
            bucket = alias_examples.setdefault(
                key,
                {"raw": raw, "count": 0, "market_ids": [], "questions": [], "source_universe": set(), "candidate_names": Counter()},
            )
            bucket["count"] = int(bucket["count"]) + 1
            bucket["source_universe"].add(market.get("source_universe", ""))
            if len(bucket["market_ids"]) < 5:
                bucket["market_ids"].append(market_id)
            if len(bucket["questions"]) < 3:
                bucket["questions"].append(market.get("question", ""))
            candidates_text = candidate_team_names(raw, pm14, aliases)
            for item in candidates_text.split(";"):
                if item:
                    bucket["candidate_names"][item] += 1

        search_start = (center - timedelta(days=7)).isoformat() if center else ""
        search_end = (center + timedelta(days=7)).isoformat() if center else ""
        audit_rows.append(
            {
                "market_id": market_id,
                "condition_id": market.get("condition_id", ""),
                "event_id": market.get("event_id", ""),
                "source_universe": market.get("source_universe", ""),
                "question": market.get("question", ""),
                "slug": market.get("slug", ""),
                "market_team_a_raw": market.get("market_team_a_raw", ""),
                "market_team_b_raw": market.get("market_team_b_raw", ""),
                "market_team_a_norm": canonical(market.get("market_team_a_raw", ""), aliases),
                "market_team_b_norm": canonical(market.get("market_team_b_raw", ""), aliases),
                "parsed_game_number": market.get("game_number", ""),
                "market_scope": market.get("market_scope", ""),
                "tournament_hint": market.get("tournament_hint", ""),
                "market_start_ts": market.get("start_ts", ""),
                "market_end_ts": market.get("end_ts", ""),
                "closed_ts": market.get("closed_ts", ""),
                "resolved_outcome": market.get("resolved_outcome", ""),
                "market_date_source": market.get("market_date_source") or date_source,
                "market_date_confidence": market.get("market_date_confidence") or date_conf,
                "search_window_start": search_start,
                "search_window_end": search_end,
                "candidate_count_before_team_filter": str(len(pm7)),
                "candidate_count_after_team_filter": str(len(team7)),
                "candidate_count_after_date_filter": str(len(date_filtered)),
                "candidate_count_after_tournament_filter": str(len(tournament_filtered)),
                "candidate_count_pm3d": str(sum(1 for g in pm3 if team_match(market, g, aliases))),
                "candidate_count_pm7d": str(len(team7)),
                "candidate_count_pm14d": str(len(team14)),
                "candidate_count_any_date_same_teams": str(len(any_date_same_teams)),
                "reject_stage": stage,
                "reject_reason": reason,
            }
        )

        if (
            market.get("market_scope") == "game_winner"
            and market.get("market_team_a_raw")
            and market.get("market_team_b_raw")
        ):
            manual_rows.append(
                {
                    "market_id": market_id,
                    "condition_id": market.get("condition_id", ""),
                    "event_id": market.get("event_id", ""),
                    "question": market.get("question", ""),
                    "slug": market.get("slug", ""),
                    "market_team_a": market.get("market_team_a_raw", ""),
                    "market_team_b": market.get("market_team_b_raw", ""),
                    "parsed_game_number": market.get("game_number", ""),
                    "tournament_hint": market.get("tournament_hint", ""),
                    "market_date": center.isoformat() if center else "",
                    "resolved_outcome": market.get("resolved_outcome", ""),
                    "candidate_opendota_matches_pm14d": candidate_match_text(team14),
                    "candidate_stratz_matches_pm14d": "",
                    "suggested_match_id": team14[0].get("match_id", "") if len(team14) == 1 else "",
                    "manual_status": "unreviewed",
                    "manual_notes": "",
                }
            )

    alias_rows = []
    for key, bucket in sorted(alias_examples.items(), key=lambda kv: (-int(kv[1]["count"]), kv[0])):
        alias_rows.append(
            {
                "raw_team_name": str(bucket["raw"]),
                "normalized_team_name": key,
                "market_count": str(bucket["count"]),
                "example_market_ids": ";".join(bucket["market_ids"]),
                "example_questions": " | ".join(bucket["questions"]),
                "source_universe": ";".join(sorted(x for x in bucket["source_universe"] if x)),
                "suggested_alias": str(bucket["raw"]),
                "candidate_opendota_team_names": ";".join(name for name, _count in bucket["candidate_names"].most_common(5)),
                "candidate_stratz_team_names": "",
            }
        )

    summary = {
        "audited_unmapped_network_markets": len(audit_rows),
        "reject_stage_counts": dict(Counter(r["reject_stage"] for r in audit_rows)),
        "source_universe_counts": dict(Counter(r["source_universe"] for r in audit_rows)),
        "markets_with_pm3d_team_candidates": sum(int(r["candidate_count_pm3d"]) > 0 for r in audit_rows),
        "markets_with_pm7d_team_candidates": sum(int(r["candidate_count_pm7d"]) > 0 for r in audit_rows),
        "markets_with_pm14d_team_candidates": sum(int(r["candidate_count_pm14d"]) > 0 for r in audit_rows),
        "markets_with_any_date_same_team_candidates": sum(
            int(r["candidate_count_any_date_same_teams"]) > 0 for r in audit_rows
        ),
        **summarize_ranges(list(markets.values()), games),
    }
    coverage = {
        "dota_universe_rows": len(games),
        "min_start_ts": summary.get("dota_universe_min_ts"),
        "max_start_ts": summary.get("dota_universe_max_ts"),
        "market_min_ts": summary.get("market_min_ts"),
        "market_max_ts": summary.get("market_max_ts"),
        "unmapped_markets_outside_dota_universe_range": outside_range_count(target_markets, games),
        "unmapped_markets_with_team_aliases_but_no_games": sum(
            bool(
                r["reject_stage"] in {"team_not_found_in_dota_universe", "dota_universe_gap"}
                and r["market_team_a_norm"]
                and r["market_team_b_norm"]
            )
            for r in audit_rows
        ),
    }
    write_csv(Path(args.audit_output), audit_rows, AUDIT_HEADERS)
    write_csv(Path(args.alias_gaps_output), alias_rows, ALIAS_GAP_HEADERS)
    write_csv(Path(args.manual_queue_output), manual_rows, MANUAL_QUEUE_HEADERS)
    Path(args.summary_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary_output).write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    Path(args.coverage_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.coverage_output).write_text(json.dumps(coverage, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"wrote {args.audit_output}")
    print(f"wrote {args.summary_output}")
    print(f"wrote {args.alias_gaps_output}")
    print(f"wrote {args.manual_queue_output}")
    print(f"wrote {args.coverage_output}")


if __name__ == "__main__":
    main()
