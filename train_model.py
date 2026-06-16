#!/usr/bin/env python3
"""Train dota_fair phase models from preprocessed training CSV."""
from __future__ import annotations

import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

TRAINING_CSV = Path("logs/training_data.csv")
OUTPUT_JOBLIB = Path("dota_fair_model/models/dota_fair.joblib")
MIN_MATCH_GROUPS = 30
MIN_SNAPSHOTS = 300
# Rows per match per phase — stratified so every phase gets equal representation per match.
# 20 × 5 phases × 1009 matches ≈ 100k rows, well within memory.
MAX_ROWS_PER_MATCH_PER_PHASE = 20


def _sample_rows(path: Path, max_per_phase: int, seed: int = 1) -> list[dict[str, Any]]:
    """Load CSV and reservoir-sample up to max_per_phase rows per (match_id, phase) bucket.

    Stratifying by phase prevents late-game-heavy matches from crowding out early-phase
    rows and ensures each phase model sees a balanced cross-section of every match.
    """
    from dota_fair_model.schemas import phase_for_duration

    rng = random.Random(seed)
    # key: (match_id, phase)
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            mid = row.get("match_id") or ""
            phase = phase_for_duration(row.get("game_time_sec")) or "unknown"
            key = (mid, phase)
            bucket = buckets[key]
            if len(bucket) < max_per_phase:
                bucket.append(row)
            else:
                idx = rng.randint(0, len(bucket))
                if idx < max_per_phase:
                    bucket[idx] = row
    return [row for bucket in buckets.values() for row in bucket]


def main() -> None:
    from dota_fair_model.train import (
        train_phase_models,
        save_artifacts,
        assert_trainable_artifact,
    )
    from dota_fair_model.schemas import PHASES

    if not TRAINING_CSV.exists():
        print(f"ERROR: {TRAINING_CSV} not found. Run preprocess_training_data.py first.", file=sys.stderr)
        sys.exit(1)

    print(f"Loading training data from {TRAINING_CSV} (max {MAX_ROWS_PER_MATCH_PER_PHASE} rows/match/phase)...")
    rows = _sample_rows(TRAINING_CSV, MAX_ROWS_PER_MATCH_PER_PHASE)
    matches = len({r.get("match_id") for r in rows if r.get("match_id")})
    print(f"  {len(rows):,} rows loaded ({matches} matches)")

    # Count by phase
    from dota_fair_model.schemas import phase_for_duration
    from collections import Counter
    phases = Counter(phase_for_duration(r.get("game_time_sec")) for r in rows)
    print("  Rows by phase:")
    for phase in PHASES:
        print(f"    {phase}: {phases.get(phase, 0):,}")

    print(f"\nTraining ExtraTreesClassifier per phase (min_groups={MIN_MATCH_GROUPS}, min_snaps={MIN_SNAPSHOTS})...")
    artifacts = train_phase_models(
        rows,
        target_name="radiant_win",
        min_match_groups=MIN_MATCH_GROUPS,
        min_snapshots=MIN_SNAPSHOTS,
        calibration_method="isotonic",
    )

    print("\nPhase metrics:")
    for phase, m in artifacts["metadata"].get("metrics", {}).items():
        if isinstance(m, dict) and "skipped" in m:
            print(f"  {phase}: SKIPPED ({m['skipped']})")
        elif isinstance(m, dict):
            brier = m.get("brier_score", "?")
            roc = m.get("roc_auc", "?")
            logloss = m.get("log_loss", "?")
            rows_c = m.get("rows", "?")
            roc_str = f"{roc:.4f}" if isinstance(roc, float) else str(roc)
            print(f"  {phase}: brier={brier:.4f} roc_auc={roc_str} log_loss={logloss:.4f} n={rows_c:,}")

    try:
        assert_trainable_artifact(artifacts)
    except RuntimeError as exc:
        print(f"\nCRITICAL: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"\nSaving model to {OUTPUT_JOBLIB}...")
    save_artifacts(artifacts, OUTPUT_JOBLIB)

    meta_path = OUTPUT_JOBLIB.with_suffix(".metadata.json")
    print(f"Metadata: {meta_path}")
    meta = json.loads(meta_path.read_text())
    print(f"  Training matches: {meta.get('training_match_count')}")
    print(f"  Feature schema: {meta.get('schema_version')}")
    print(f"\nModel ready at {OUTPUT_JOBLIB}")


if __name__ == "__main__":
    main()
