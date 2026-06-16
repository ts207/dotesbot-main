#!/usr/bin/env python3
"""Fetch missing OpenDota radiant_win outcomes for mapped Polymarket markets."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

import yaml


def fetch_match(match_id: str) -> dict | None:
    req = urllib.request.Request(
        f"https://api.opendota.com/api/matches/{match_id}",
        headers={"User-Agent": "curl/8"},
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markets", default="markets.yaml")
    parser.add_argument("--outcomes", default="logs/opendota_outcomes.json")
    parser.add_argument("--sleep-sec", type=float, default=1.05)
    parser.add_argument("--only-map-winner", action="store_true", default=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    data = yaml.safe_load(Path(args.markets).read_text(encoding="utf-8")) or {}
    markets = data.get("markets", [])
    ids = []
    for market in markets:
        if args.only_map_winner and market.get("market_type") != "MAP_WINNER":
            continue
        match_id = str(market.get("dota_match_id") or "")
        if match_id.isdigit():
            ids.append(match_id)
    ids = sorted(set(ids))

    out = Path(args.outcomes)
    outcomes = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    missing = [mid for mid in ids if mid not in outcomes]
    if args.limit:
        missing = missing[: args.limit]

    errors = []
    print(f"mapped_ids={len(ids)} existing={len(outcomes)} missing={len(missing)}")
    for idx, match_id in enumerate(missing, start=1):
        try:
            match = fetch_match(match_id)
            radiant_win = match.get("radiant_win") if isinstance(match, dict) else None
            if radiant_win is None:
                errors.append({"match_id": match_id, "error": "missing_radiant_win"})
            else:
                outcomes[match_id] = bool(radiant_win)
        except Exception as exc:
            errors.append({"match_id": match_id, "error": type(exc).__name__, "detail": str(exc)[:200]})
        if idx % 10 == 0 or idx == len(missing):
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(outcomes, indent=2, sort_keys=True), encoding="utf-8")
            print(f"progress={idx}/{len(missing)} outcomes={len(outcomes)} errors={len(errors)}", flush=True)
        time.sleep(args.sleep_sec)

    out.write_text(json.dumps(outcomes, indent=2, sort_keys=True), encoding="utf-8")
    err_path = out.with_name(out.stem + "_fetch_errors.json")
    err_path.write_text(json.dumps(errors, indent=2), encoding="utf-8")
    print(f"done outcomes={len(outcomes)} errors={len(errors)}")
    print(f"wrote {out}")
    print(f"wrote {err_path}")


if __name__ == "__main__":
    main()
