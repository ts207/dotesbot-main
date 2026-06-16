# cold_storage/

Immutable historical data archives. Holds pre-migration baselines and any
"data we should never lose but won't query day-to-day."

## Layout

```
cold_storage/
├── README.md                        # this file
├── baseline_<YYYY_MM_DD>/           # one snapshot per migration milestone
│   ├── README.md                    # what's in this baseline, why captured
│   ├── MANIFEST.sha256              # tamper-evident checksums
│   └── <files>                      # the actual archived data
└── ...
```

## Current contents

- `baseline_2026_05_28/` — snapshot taken at the start of the data-
  consolidation work. Contains all `logs/*.bak` files at that point
  (the only authoritative source for the 2026-05-15 → 05-23 tournament
  history before CSV rotation overwrote it).

## Rules

1. **Never modify** files in any `baseline_*/` subdirectory. Verify
   integrity with `sha256sum -c MANIFEST.sha256`.
2. Each baseline directory must include its own `README.md` and
   `MANIFEST.sha256` — these are checked into git; the data files are not.
3. To add a new baseline, create `baseline_<YYYY_MM_DD>/`, copy the
   files, generate `sha256sum *.bak > MANIFEST.sha256`, write a
   `README.md` describing what changed and why this baseline is needed.

## What's not here

- Live operational data: `logs/` and `data_v2/`.
- The 16 GB `logs/liveleague_raw.jsonl` — too large to duplicate.
  Will be parsed into `data_v2/snapshots/` during Phase 1 backfill,
  then either truncated or moved here in compressed form.
