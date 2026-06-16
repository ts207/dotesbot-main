#!/usr/bin/env python3
"""Build v0 hero traits from parsed OpenDota player match details.

OpenDota `lane_role` is a lane assignment, not Dota position 1-5. Modern pro
support players are usually represented as lane roles 1/2/3 with their lane, not
as pos4/pos5. This builder therefore emits:
1. role-specific lane-role traits where enough samples exist;
2. global hero fallback traits for every sufficiently sampled hero.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


CORE_FIELDS = ("gold_t", "xp_t", "lh_t", "lane_role")


def at(values: list[float] | None, minute: int) -> float | None:
    if not values or len(values) <= minute:
        return None
    value = values[minute]
    return float(value) if value is not None else None


def load_player_rows(path: Path, min_duration_min: float) -> pd.DataFrame:
    matches = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for match_id, match in matches.items():
        duration_min = (match.get("duration") or 0) / 60.0
        if duration_min < min_duration_min:
            continue
        patch = match.get("patch")
        players = match.get("players") or []
        if len(players) != 10:
            continue
        for player in players:
            hero_id = player.get("hero_id")
            role = player.get("lane_role")
            slot = player.get("player_slot")
            gold_t = player.get("gold_t") or []
            xp_t = player.get("xp_t") or []
            lh_t = player.get("lh_t") or []
            if hero_id is None or role is None or slot is None:
                continue
            if len(gold_t) < 25 or len(xp_t) < 25 or len(lh_t) < 25:
                continue
            is_radiant = int(slot) < 128
            g10 = at(gold_t, 10)
            g20 = at(gold_t, 20)
            g30 = at(gold_t, 30)
            x10 = at(xp_t, 10)
            x20 = at(xp_t, 20)
            lh10 = at(lh_t, 10)
            lh20 = at(lh_t, 20)
            rows.append(
                {
                    "match_id": str(match.get("match_id") or match_id),
                    "patch": patch,
                    "hero_id": int(hero_id),
                    "role": int(role),
                    "is_radiant": bool(is_radiant),
                    "duration_min": duration_min,
                    "gold_t": gold_t,
                    "xp_t": xp_t,
                    "lh_t": lh_t,
                    "gold_10": g10,
                    "gold_20": g20,
                    "gold_30": g30,
                    "xp_10": x10,
                    "xp_20": x20,
                    "lh_10": lh10,
                    "lh_20": lh20,
                    "kills": player.get("kills") or 0,
                    "assists": player.get("assists") or 0,
                    "deaths": player.get("deaths") or 0,
                    "tower_per_min": (player.get("tower_damage") or 0) / duration_min if duration_min > 0 else 0.0,
                    "hero_dmg_per_min": (player.get("hero_damage") or 0) / duration_min if duration_min > 0 else 0.0,
                    "teamfight_participation": player.get("teamfight_participation"),
                }
            )
    return pd.DataFrame(rows)


def add_team_shares(df: pd.DataFrame) -> pd.DataFrame:
    team_gold = (
        df.groupby(["match_id", "is_radiant"], as_index=False)
        .agg(team_gold_10=("gold_10", "sum"), team_gold_30=("gold_30", "sum"))
    )
    df = df.merge(team_gold, on=["match_id", "is_radiant"], how="left")
    df["gold_share_10"] = df["gold_10"] / df["team_gold_10"].replace(0, np.nan)
    df["gold_share_30"] = df["gold_30"] / df["team_gold_30"].replace(0, np.nan)
    df["scaling_raw"] = df["gold_share_30"] - df["gold_share_10"]
    df["early_gpm_accel"] = ((df["gold_20"] - df["gold_10"]) / 10.0) - (df["gold_10"] / 10.0)
    df["early_xpm_accel"] = ((df["xp_20"] - df["xp_10"]) / 10.0) - (df["xp_10"] / 10.0)
    df["early_lh_accel"] = (df["lh_20"] - df["lh_10"]) / 10.0
    return df


def percentile_scores(traits: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    score_inputs = {
        "scaling_score": "scaling_mean",
        "tempo_score": "tempo_proxy_mean",
        "tower_score": "tower_mean",
        "fight_score": "fight_mean",
        "volatility_score": "scaling_std",
    }
    for score_col, source_col in score_inputs.items():
        traits[score_col] = traits.groupby(group_cols)[source_col].rank(pct=True)
    return traits


def build_role_traits(df: pd.DataFrame, min_games_by_role: dict[int, int]) -> pd.DataFrame:
    traits = (
        df.groupby(["patch", "hero_id", "role"], as_index=False)
        .agg(
            games=("match_id", "count"),
            scaling_mean=("scaling_raw", "mean"),
            scaling_std=("scaling_raw", "std"),
            tempo_proxy_mean=("tempo_proxy", "mean"),
            tower_mean=("tower_per_min", "mean"),
            fight_mean=("fight_proxy", "mean"),
            duration_mean=("duration_min", "mean"),
        )
    )
    traits["min_games_required"] = traits["role"].map(min_games_by_role).fillna(5).astype(int)
    traits = traits[traits["games"] >= traits["min_games_required"]].copy()
    traits["trait_scope"] = "lane_role"
    return percentile_scores(traits, ["patch", "role"])


def build_global_traits(df: pd.DataFrame, min_games: int) -> pd.DataFrame:
    traits = (
        df.groupby(["patch", "hero_id"], as_index=False)
        .agg(
            games=("match_id", "count"),
            scaling_mean=("scaling_raw", "mean"),
            scaling_std=("scaling_raw", "std"),
            tempo_proxy_mean=("tempo_proxy", "mean"),
            tower_mean=("tower_per_min", "mean"),
            fight_mean=("fight_proxy", "mean"),
            duration_mean=("duration_min", "mean"),
        )
    )
    traits = traits[traits["games"] >= min_games].copy()
    traits["role"] = 0
    traits["min_games_required"] = min_games
    traits["trait_scope"] = "global_hero"
    return percentile_scores(traits, ["patch"])


def build_traits(df: pd.DataFrame, min_games: int, support_min_games: int, global_min_games: int) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    df = add_team_shares(df)
    df["tempo_proxy"] = df[["early_gpm_accel", "early_xpm_accel", "early_lh_accel"]].mean(axis=1)
    df["fight_proxy"] = df["hero_dmg_per_min"]
    if "teamfight_participation" in df and df["teamfight_participation"].notna().any():
        df["fight_proxy"] = df[["hero_dmg_per_min", "teamfight_participation"]].mean(axis=1)

    min_games_by_role = {1: min_games, 2: min_games, 3: min_games, 4: support_min_games, 5: support_min_games}
    role_traits = build_role_traits(df, min_games_by_role)
    global_traits = build_global_traits(df, global_min_games)
    return pd.concat([role_traits, global_traits], ignore_index=True, sort=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="logs/opendota_player_match_details.json")
    parser.add_argument("--output", default="data/hero_role_traits.csv")
    parser.add_argument("--player-rows-output", default="data/hero_role_player_rows.csv")
    parser.add_argument("--min-duration-min", type=float, default=15.0)
    parser.add_argument("--min-games", type=int, default=5)
    parser.add_argument("--support-min-games", type=int, default=2)
    parser.add_argument("--global-min-games", type=int, default=3)
    args = parser.parse_args()

    df = load_player_rows(Path(args.input), args.min_duration_min)
    traits = build_traits(df, args.min_games, args.support_min_games, args.global_min_games)

    out_rows = Path(args.player_rows_output)
    out_rows.parent.mkdir(parents=True, exist_ok=True)
    df.drop(columns=["gold_t", "xp_t", "lh_t"], errors="ignore").to_csv(out_rows, index=False)

    out_traits = Path(args.output)
    out_traits.parent.mkdir(parents=True, exist_ok=True)
    traits.to_csv(out_traits, index=False)

    summary = {
        "input_matches": len(json.loads(Path(args.input).read_text(encoding="utf-8"))),
        "player_rows": int(len(df)),
        "trait_rows": int(len(traits)),
        "role_trait_rows": int((traits["trait_scope"] == "lane_role").sum()) if not traits.empty else 0,
        "global_fallback_rows": int((traits["trait_scope"] == "global_hero").sum()) if not traits.empty else 0,
        "min_games": args.min_games,
        "support_min_games": args.support_min_games,
        "global_min_games": args.global_min_games,
        "limitations": [
            "OpenDota lane_role is lane assignment, not Dota position 1-5; role 0 rows are global hero fallbacks",
            "lane_score omitted: lane opponent matching is not implemented in v0",
            "tempo_score uses early economy acceleration proxy, not timestamped kill participation",
            "single cached patch corpus only; rolling 180-day snapshots require a larger dated corpus",
        ],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"wrote {out_traits}")
    print(f"wrote {out_rows}")


if __name__ == "__main__":
    main()
