#!/usr/bin/env python3
"""Map normalized Polymarket Dota markets to Dota game rows."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path


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
    "market_date_source",
    "market_date_confidence",
    "game_id",
    "match_id",
    "series_id",
    "yes_team_id",
    "no_team_id",
    "radiant_team_id",
    "dire_team_id",
    "team_a_is_radiant",
    "team_a_win",
    "mapping_confidence",
    "mapping_method",
    "mapping_notes",
    "scope",
    "duplicate_resolution",
    "market_volume",
    "market_liquidity",
]


CANDIDATE_HEADERS = [
    "market_id",
    "condition_id",
    "event_id",
    "match_id",
    "game_id",
    "series_id",
    "market_team_a_raw",
    "market_team_b_raw",
    "market_team_a_norm",
    "market_team_b_norm",
    "dota_radiant_team_name",
    "dota_dire_team_name",
    "dota_radiant_team_id",
    "dota_dire_team_id",
    "team_match_score",
    "date_distance_hours",
    "tournament_match_score",
    "game_number_match_score",
    "result_match_score",
    "scope_match_score",
    "candidate_score",
    "mapping_confidence",
    "mapping_decision",
    "mapping_reject_reason",
    "team_a_is_radiant",
    "team_a_win",
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


def load_details(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def clean_to_market(row: dict[str, str]) -> dict[str, str]:
    return {
        "market_id": row.get("market_id", ""),
        "condition_id": row.get("condition_id", ""),
        "event_id": row.get("event_id", ""),
        "yes_token_id": row.get("yes_token_id") or row.get("token_id_yes", ""),
        "no_token_id": row.get("no_token_id") or row.get("token_id_no", ""),
        "slug": row.get("slug", ""),
        "question": row.get("question", "") or f"{row.get('team_a', '')} vs {row.get('team_b', '')}".strip(),
        "source_universe": "local_clean_v2",
        "market_discovery_source": "local_clean_v2",
        "discovery_query": "",
        "match_id": row.get("match_id", ""),
        "team_a_id": row.get("team_a_id", ""),
        "team_b_id": row.get("team_b_id", ""),
        "candidate_team_a": row.get("team_a", ""),
        "candidate_team_b": row.get("team_b", ""),
        "team_a_is_radiant": row.get("team_a_is_radiant", ""),
        "team_a_win": row.get("team_a_win", ""),
        "market_volume": row.get("volume", ""),
        "market_liquidity": row.get("liquidity", ""),
        "market_scope": "game_winner",
        "game_number": row.get("game_number", ""),
    }


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


def extract_match_id(row: dict[str, str]) -> str:
    for key in ("match_id", "dota_match_id", "game_id"):
        value = str(row.get(key) or "")
        if value.isdigit() and len(value) >= 8:
            return value
    text = " ".join(str(row.get(k) or "") for k in ("slug", "question", "description"))
    match = re.search(r"\b(\d{8,12})\b", text)
    return match.group(1) if match else ""


def load_market_universe(clean_v2: Path, network_universe: Path) -> list[dict[str, str]]:
    by_market: dict[str, dict[str, str]] = {}
    for row in read_csv(clean_v2):
        market = clean_to_market(row)
        if market.get("market_id"):
            by_market[market["market_id"]] = market
    for row in read_csv(network_universe):
        if not row.get("market_id"):
            continue
        by_market.setdefault(row["market_id"], row)
    return list(by_market.values())


def load_aliases(path: Path) -> dict[str, str]:
    aliases = {}
    for row in read_csv(path):
        canonical = norm(row.get("canonical_team_name") or row.get("alias"))
        alias = norm(row.get("alias"))
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


def market_yes_no(market: dict[str, str]) -> tuple[str, str]:
    return (
        market.get("candidate_team_a") or market.get("market_team_a_raw") or "",
        market.get("candidate_team_b") or market.get("market_team_b_raw") or "",
    )


def resolved_winner(market: dict[str, str], yes_raw: str, no_raw: str, aliases: dict[str, str]) -> str:
    resolved = market.get("resolved_outcome", "")
    if not resolved:
        return ""
    if name_score(resolved, yes_raw, aliases) >= 0.92:
        return "team_a"
    if name_score(resolved, no_raw, aliases) >= 0.92:
        return "team_b"
    return "unknown"


def game_start(game: dict[str, str]) -> datetime | None:
    return parse_ts(game.get("start_ts"))


def market_date(market: dict[str, str]) -> datetime | None:
    return parse_ts(market.get("event_date_hint")) or parse_ts(market.get("start_ts")) or parse_ts(market.get("end_ts"))


def infer_game_numbers(games: list[dict[str, str]], aliases: dict[str, str]) -> dict[str, int]:
    groups: dict[tuple[str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    for game in games:
        dt = game_start(game)
        if not dt:
            continue
        teams = sorted(
            [
                canonical(game.get("radiant_team_name", ""), aliases),
                canonical(game.get("dire_team_name", ""), aliases),
            ]
        )
        key = (dt.date().isoformat(), game.get("league_id", ""), teams[0], teams[1])
        groups[key].append(game)
    out = {}
    for rows in groups.values():
        rows.sort(key=lambda r: int(float(r.get("start_ts") or 0)))
        for idx, row in enumerate(rows, start=1):
            out[row["match_id"]] = idx
    return out


def local_map_row(market: dict[str, str], detail: dict | None) -> dict[str, str]:
    match_id = extract_match_id(market)
    radiant_id = str(detail.get("radiant_team_id") or "") if detail else ""
    dire_id = str(detail.get("dire_team_id") or "") if detail else ""
    return {
        "market_id": market.get("market_id", ""),
        "condition_id": market.get("condition_id", ""),
        "event_id": market.get("event_id", ""),
        "yes_token_id": market.get("yes_token_id") or market.get("token_id_yes", ""),
        "no_token_id": market.get("no_token_id") or market.get("token_id_no", ""),
        "slug": market.get("slug", ""),
        "question": market.get("question", ""),
        "source_universe": "local_clean_v2",
        "market_discovery_source": market.get("market_discovery_source", "local_clean_v2"),
        "discovery_query": market.get("discovery_query", ""),
        "market_date_source": "",
        "market_date_confidence": "",
        "game_id": market.get("market_id", ""),
        "match_id": match_id,
        "series_id": match_id,
        "yes_team_id": market.get("team_a_id", ""),
        "no_team_id": market.get("team_b_id", ""),
        "radiant_team_id": radiant_id,
        "dire_team_id": dire_id,
        "team_a_is_radiant": market.get("team_a_is_radiant", ""),
        "team_a_win": market.get("team_a_win", ""),
        "mapping_confidence": "1.00" if market.get("team_a_is_radiant") in {"0", "1"} else "0.00",
        "mapping_method": "clean_v2_direct",
        "mapping_notes": "" if market.get("team_a_is_radiant") in {"0", "1"} else "missing_team_a_is_radiant",
        "scope": "game",
        "duplicate_resolution": "market_id_primary_key",
        "market_volume": market.get("market_volume", ""),
        "market_liquidity": market.get("market_liquidity", ""),
    }


def candidate_for(market: dict[str, str], game: dict[str, str], aliases: dict[str, str], game_numbers: dict[str, int]) -> dict[str, str]:
    yes_raw, no_raw = market_yes_no(market)
    yes_rad = name_score(yes_raw, game.get("radiant_team_name", ""), aliases)
    no_dire = name_score(no_raw, game.get("dire_team_name", ""), aliases)
    yes_dire = name_score(yes_raw, game.get("dire_team_name", ""), aliases)
    no_rad = name_score(no_raw, game.get("radiant_team_name", ""), aliases)
    radiant_orientation_score = (yes_rad + no_dire) / 2.0
    dire_orientation_score = (yes_dire + no_rad) / 2.0
    team_a_is_radiant = radiant_orientation_score >= dire_orientation_score
    team_score = max(radiant_orientation_score, dire_orientation_score)

    mdt, gdt = market_date(market), game_start(game)
    hours = abs((mdt - gdt).total_seconds()) / 3600.0 if mdt and gdt else 9999.0
    date_score = 1.0 if hours <= 24 else 0.85 if hours <= 72 else 0.55 if hours <= 168 else 0.0
    tournament_score = 0.5
    if market.get("tournament_hint") and game.get("tournament_name"):
        tournament_score = name_score(market.get("tournament_hint", ""), game.get("tournament_name", ""), aliases)

    parsed_game = int(market.get("game_number") or 0)
    inferred_game = game_numbers.get(game.get("match_id", ""), 0)
    if parsed_game and inferred_game:
        game_number_score = 1.0 if parsed_game == inferred_game else 0.0
    else:
        game_number_score = 0.5

    scope_score = 1.0 if market.get("market_scope") == "game_winner" else 0.0
    resolved = resolved_winner(market, yes_raw, no_raw, aliases)
    result_score = 0.5
    reject_reason = []
    if resolved in {"team_a", "team_b"} and game.get("winner_team_id"):
        yes_team_id = game.get("radiant_team_id") if team_a_is_radiant else game.get("dire_team_id")
        team_a_win = game.get("winner_team_id") == yes_team_id
        result_score = 1.0 if (resolved == "team_a") == team_a_win else 0.0
        if result_score == 0.0:
            reject_reason.append("result_mismatch")
    elif resolved == "unknown":
        reject_reason.append("resolved_outcome_unknown_team")

    score = (
        0.35 * team_score
        + 0.20 * date_score
        + 0.15 * result_score
        + 0.15 * game_number_score
        + 0.10 * tournament_score
        + 0.05 * scope_score
    )
    if market.get("market_scope") != "game_winner":
        reject_reason.append("scope_mismatch")
    if team_score < 0.86:
        reject_reason.append("team_mismatch")
    if hours > 168:
        reject_reason.append("date_window_mismatch")
    if parsed_game and inferred_game and parsed_game != inferred_game:
        reject_reason.append("game_number_mismatch")

    confidence = 0.0
    if not reject_reason:
        if team_score >= 0.98 and hours <= 24 and result_score == 1.0 and game_number_score == 1.0:
            confidence = 1.0
        elif team_score >= 0.95 and hours <= 72 and result_score == 1.0 and game_number_score >= 0.5:
            confidence = 0.95
        elif team_score >= 0.90 and hours <= 168:
            confidence = 0.80
    radiant_win = game.get("radiant_win", "")
    if radiant_win in {"0", "1"}:
        candidate_team_a_win = radiant_win if team_a_is_radiant else str(int(radiant_win == "0"))
    else:
        candidate_team_a_win = ""

    return {
        "market_id": market.get("market_id", ""),
        "condition_id": market.get("condition_id", ""),
        "event_id": market.get("event_id", ""),
        "match_id": game.get("match_id", ""),
        "game_id": game.get("game_id", game.get("match_id", "")),
        "series_id": game.get("series_id", game.get("match_id", "")),
        "market_team_a_raw": yes_raw,
        "market_team_b_raw": no_raw,
        "market_team_a_norm": canonical(yes_raw, aliases),
        "market_team_b_norm": canonical(no_raw, aliases),
        "dota_radiant_team_name": game.get("radiant_team_name", ""),
        "dota_dire_team_name": game.get("dire_team_name", ""),
        "dota_radiant_team_id": game.get("radiant_team_id", ""),
        "dota_dire_team_id": game.get("dire_team_id", ""),
        "team_match_score": f"{team_score:.4f}",
        "date_distance_hours": f"{hours:.2f}",
        "tournament_match_score": f"{tournament_score:.4f}",
        "game_number_match_score": f"{game_number_score:.4f}",
        "result_match_score": f"{result_score:.4f}",
        "scope_match_score": f"{scope_score:.4f}",
        "candidate_score": f"{score:.4f}",
        "mapping_confidence": f"{confidence:.2f}",
        "mapping_decision": "candidate",
        "mapping_reject_reason": ",".join(reject_reason),
        "team_a_is_radiant": "1" if team_a_is_radiant else "0",
        "team_a_win": candidate_team_a_win,
    }


def network_map_row(market: dict[str, str], cand: dict[str, str] | None, reason: str) -> dict[str, str]:
    if not cand:
        return {
            "market_id": market.get("market_id", ""),
            "condition_id": market.get("condition_id", ""),
            "event_id": market.get("event_id", ""),
            "yes_token_id": market.get("yes_token_id", ""),
            "no_token_id": market.get("no_token_id", ""),
            "slug": market.get("slug", ""),
            "question": market.get("question", ""),
            "source_universe": market.get("source_universe", ""),
            "market_discovery_source": market.get("market_discovery_source", ""),
            "discovery_query": market.get("discovery_query", ""),
            "market_date_source": market.get("market_date_source", ""),
            "market_date_confidence": market.get("market_date_confidence", ""),
            "game_id": market.get("market_id", ""),
            "match_id": "",
            "series_id": market.get("event_id", "") or market.get("market_id", ""),
            "yes_team_id": "",
            "no_team_id": "",
            "radiant_team_id": "",
            "dire_team_id": "",
            "team_a_is_radiant": "",
            "team_a_win": "",
            "mapping_confidence": "0.00",
            "mapping_method": "network_candidate_scoring",
            "mapping_notes": reason,
            "scope": market.get("market_scope", "ambiguous"),
            "duplicate_resolution": "market_id_primary_key",
            "market_volume": market.get("volume", ""),
            "market_liquidity": market.get("liquidity", ""),
        }
    team_a_is_radiant = cand.get("team_a_is_radiant", "")
    yes_team_id = cand["dota_radiant_team_id"] if team_a_is_radiant == "1" else cand["dota_dire_team_id"]
    no_team_id = cand["dota_dire_team_id"] if team_a_is_radiant == "1" else cand["dota_radiant_team_id"]
    team_a_win = cand.get("team_a_win", "")
    return {
        "market_id": market.get("market_id", ""),
        "condition_id": market.get("condition_id", ""),
        "event_id": market.get("event_id", ""),
        "yes_token_id": market.get("yes_token_id", ""),
        "no_token_id": market.get("no_token_id", ""),
        "slug": market.get("slug", ""),
        "question": market.get("question", ""),
        "source_universe": market.get("source_universe", ""),
        "market_discovery_source": market.get("market_discovery_source", ""),
        "discovery_query": market.get("discovery_query", ""),
        "market_date_source": market.get("market_date_source", ""),
        "market_date_confidence": market.get("market_date_confidence", ""),
        "game_id": market.get("market_id", ""),
        "match_id": cand.get("match_id", ""),
        "series_id": cand.get("series_id", cand.get("match_id", "")),
        "yes_team_id": yes_team_id,
        "no_team_id": no_team_id,
        "radiant_team_id": cand.get("dota_radiant_team_id", ""),
        "dire_team_id": cand.get("dota_dire_team_id", ""),
        "team_a_is_radiant": team_a_is_radiant,
        "team_a_win": team_a_win,
        "mapping_confidence": cand.get("mapping_confidence", "0.00"),
        "mapping_method": "network_candidate_scoring",
        "mapping_notes": reason,
        "scope": "game",
        "duplicate_resolution": "market_id_primary_key",
        "market_volume": market.get("volume", ""),
        "market_liquidity": market.get("liquidity", ""),
    }


def game_date_index(games: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    index = defaultdict(list)
    for game in games:
        dt = game_start(game)
        if dt:
            index[dt.date().isoformat()].append(game)
    return index


def nearby_games(index: dict[str, list[dict[str, str]]], dt: datetime | None, days: int) -> list[dict[str, str]]:
    if not dt:
        return [g for rows in index.values() for g in rows]
    out = []
    for offset in range(-days, days + 1):
        key = (dt.date()).toordinal() + offset
        day = datetime.fromordinal(key).date().isoformat()
        out.extend(index.get(day, []))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean-v2", default="data/clean_v2/matches.csv")
    parser.add_argument("--market-universe", default="data/processed/polymarket/dota_market_universe.csv")
    parser.add_argument("--dota-details", default="logs/opendota_player_match_details.json")
    parser.add_argument("--dota-game-universe", default="data/processed/dota_game_universe.csv")
    parser.add_argument("--team-aliases", default="data/manual/team_aliases.csv")
    parser.add_argument("--output", default="data/processed/market_game_map.csv")
    parser.add_argument("--audit-output", default="reports/market_mapping_audit.csv")
    parser.add_argument("--candidate-output", default="reports/network_mapping_candidates.csv")
    parser.add_argument("--summary-output", default="reports/network_mapping_summary.json")
    parser.add_argument("--date-window-days", type=int, default=7)
    args = parser.parse_args()

    details = load_details(Path(args.dota_details))
    markets = load_market_universe(Path(args.clean_v2), Path(args.market_universe))
    aliases = load_aliases(Path(args.team_aliases))
    games = read_csv(Path(args.dota_game_universe))
    game_numbers = infer_game_numbers(games, aliases)
    date_index = game_date_index(games)
    rows = []
    candidates = []
    summary = Counter()

    for market in markets:
        if not market.get("market_id"):
            continue
        if market.get("source_universe") == "local_clean_v2":
            rows.append(local_map_row(market, details.get(extract_match_id(market))))
            continue

        summary["network_markets_total"] += 1
        yes_raw, no_raw = market_yes_no(market)
        if yes_raw and no_raw:
            summary["network_markets_with_parsed_teams"] += 1
        if market.get("market_scope") != "game_winner":
            summary["network_rejected_scope_mismatch"] += 1
            rows.append(network_map_row(market, None, "scope_mismatch"))
            continue
        if not (yes_raw and no_raw):
            summary["network_rejected_no_dota_candidate"] += 1
            rows.append(network_map_row(market, None, "team_names_unparsed"))
            continue

        scoped_games = nearby_games(date_index, market_date(market), args.date_window_days)
        cand_rows = [candidate_for(market, game, aliases, game_numbers) for game in scoped_games]
        cand_rows = [c for c in cand_rows if float(c["team_match_score"]) >= 0.86]
        candidates.extend(cand_rows)
        if not cand_rows:
            summary["network_rejected_no_dota_candidate"] += 1
            rows.append(network_map_row(market, None, "no_dota_candidate"))
            continue
        summary["network_markets_with_candidate_dota_games"] += 1
        cand_rows.sort(key=lambda c: float(c["candidate_score"]), reverse=True)
        top = cand_rows[0]
        second = cand_rows[1] if len(cand_rows) > 1 else None
        if second and float(top["candidate_score"]) - float(second["candidate_score"]) < 0.05:
            top["mapping_decision"] = "rejected"
            top["mapping_reject_reason"] = (top["mapping_reject_reason"] + ",ambiguous").strip(",")
            summary["network_rejected_ambiguous"] += 1
            rows.append(network_map_row(market, None, "ambiguous"))
            continue
        if "result_mismatch" in top["mapping_reject_reason"]:
            summary["network_rejected_result_mismatch"] += 1
            rows.append(network_map_row(market, None, "result_mismatch"))
            continue
        conf = float(top["mapping_confidence"])
        if conf == 1.0:
            summary["network_mapped_conf_1_00"] += 1
        elif conf == 0.95:
            summary["network_mapped_conf_0_95"] += 1
        elif conf > 0:
            summary["network_mapped_below_0_95"] += 1
        if conf >= 0.95:
            top["mapping_decision"] = "accepted"
            rows.append(network_map_row(market, top, "accepted"))
        else:
            rows.append(network_map_row(market, None, top["mapping_reject_reason"] or "below_confidence_threshold"))

    write_csv(Path(args.output), rows, HEADERS)
    write_csv(Path(args.audit_output), rows, HEADERS)
    write_csv(Path(args.candidate_output), candidates, CANDIDATE_HEADERS)
    mapped_by_source = Counter(r.get("source_universe") or "unknown" for r in rows if float(r.get("mapping_confidence") or 0) >= 0.95)
    rows_by_source = Counter(r.get("source_universe") or "unknown" for r in rows)
    mapped_by_query = Counter((r.get("discovery_query") or r.get("market_discovery_source") or "unknown") for r in rows if float(r.get("mapping_confidence") or 0) >= 0.95)
    summary_dict = {
        "network_markets_total": summary["network_markets_total"],
        "network_markets_with_parsed_teams": summary["network_markets_with_parsed_teams"],
        "network_markets_with_candidate_dota_games": summary["network_markets_with_candidate_dota_games"],
        "network_mapped_conf_1_00": summary["network_mapped_conf_1_00"],
        "network_mapped_conf_0_95": summary["network_mapped_conf_0_95"],
        "network_mapped_below_0_95": summary["network_mapped_below_0_95"],
        "network_rejected_ambiguous": summary["network_rejected_ambiguous"],
        "network_rejected_result_mismatch": summary["network_rejected_result_mismatch"],
        "network_rejected_scope_mismatch": summary["network_rejected_scope_mismatch"],
        "network_rejected_no_dota_candidate": summary["network_rejected_no_dota_candidate"],
        "mapped_by_source_universe": dict(mapped_by_source),
        "mapped_by_query": dict(mapped_by_query),
        "rows_by_source_universe": dict(rows_by_source),
    }
    Path(args.summary_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary_output).write_text(json.dumps(summary_dict, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary_dict, indent=2, sort_keys=True))
    print(f"wrote {args.output}")
    print(f"wrote {args.candidate_output}")
    print(f"wrote {args.summary_output}")


if __name__ == "__main__":
    main()
