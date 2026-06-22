from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pandas as pd


DEFAULT_SETTLEMENTS = {
    "2547073": "Team Spirit",
    "2547074": "Team Spirit",
    "2547079": "Team Spirit",
    "2618139": "OG",
    "2618141": "OG",
    "2547097": "Enjoy",
    "2547098": "Enjoy",
    "2547103": "Enjoy",
    "2547123": "Nigma Galaxy",
    "2547124": "Nigma Galaxy",
    "2547125": "Nigma Galaxy",
    "2547143": "Natus Vincere",
    "2547144": "Natus Vincere",
    "2547145": "Natus Vincere",
    "2618140": "OG",
}


def _norm(value: object) -> str:
    return str(value or "").strip().casefold()


def _settlement_for_token(row: pd.Series) -> object:
    token_id = str(row.get("token_id") or "")
    if token_id == str(row.get("yes_token_id") or ""):
        return row.get("settled_yes_outcome")
    if token_id == str(row.get("no_token_id") or ""):
        return row.get("settled_no_outcome")
    return row.get("settlement_outcome")


def load_settlements(path: Path | None) -> dict[str, str]:
    if path is None:
        return dict(DEFAULT_SETTLEMENTS)
    with path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return {str(k): str(v) for k, v in raw.items()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-file", default="data_v2/model_value_replay.parquet")
    parser.add_argument("--settlements-json", type=Path)
    parser.add_argument("--backup-suffix", default=".pre_settlement_fill_20260622")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    replay_path = Path(args.replay_file)
    settlements = load_settlements(args.settlements_json)

    df = pd.read_parquet(replay_path)
    before_missing = int(df["settled_yes_outcome"].isna().sum())
    before_terminal_missing = int(
        df.sort_values("timestamp_ns").groupby("dota_match_id").tail(1)["settled_yes_outcome"].isna().sum()
    )

    fills: list[dict[str, object]] = []
    market_ids = df["market_id"].astype(str)
    for market_id, winner in settlements.items():
        mask = market_ids == str(market_id)
        if not mask.any():
            continue

        market_rows = df.loc[mask, ["yes_team", "no_team"]].drop_duplicates()
        if market_rows.empty:
            continue
        yes_team = str(market_rows.iloc[0]["yes_team"])
        no_team = str(market_rows.iloc[0]["no_team"])

        if _norm(winner) == _norm(yes_team):
            yes_outcome, no_outcome = "WIN", "LOSS"
        elif _norm(winner) == _norm(no_team):
            yes_outcome, no_outcome = "LOSS", "WIN"
        else:
            raise ValueError(
                f"Winner {winner!r} does not match yes/no teams for market {market_id}: "
                f"{yes_team!r} / {no_team!r}"
            )

        fill_mask = mask & df["settled_yes_outcome"].isna()
        if not fill_mask.any():
            continue

        df.loc[fill_mask, "resolved_outcome"] = winner
        df.loc[fill_mask, "settled_yes_outcome"] = yes_outcome
        df.loc[fill_mask, "settled_no_outcome"] = no_outcome
        df.loc[fill_mask, "settlement_outcome"] = df.loc[fill_mask].apply(_settlement_for_token, axis=1)

        fills.append(
            {
                "market_id": market_id,
                "winner": winner,
                "yes_team": yes_team,
                "no_team": no_team,
                "yes_outcome": yes_outcome,
                "rows_filled": int(fill_mask.sum()),
                "matches": sorted(df.loc[fill_mask, "dota_match_id"].astype(str).unique()),
            }
        )

    if not args.no_backup:
        backup_path = replay_path.with_suffix(replay_path.suffix + args.backup_suffix)
        if not backup_path.exists():
            shutil.copy2(replay_path, backup_path)

    df.to_parquet(replay_path, index=False)

    after_missing = int(df["settled_yes_outcome"].isna().sum())
    after_terminal_missing = int(
        df.sort_values("timestamp_ns").groupby("dota_match_id").tail(1)["settled_yes_outcome"].isna().sum()
    )
    print(
        json.dumps(
            {
                "replay_file": str(replay_path),
                "fills": fills,
                "rows_missing_before": before_missing,
                "rows_missing_after": after_missing,
                "terminal_matches_missing_before": before_terminal_missing,
                "terminal_matches_missing_after": after_terminal_missing,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
