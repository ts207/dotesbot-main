#!/usr/bin/env python3
"""Run the Model B v2 no-Dota-candidate audit with v2 output names."""

from __future__ import annotations

import subprocess
import sys


def main() -> None:
    cmd = [
        sys.executable,
        "scripts/audit_no_dota_candidates.py",
        "--audit-output",
        "reports/model_b_v2_no_candidate_audit.csv",
        "--summary-output",
        "reports/model_b_v2_no_candidate_summary.json",
        "--alias-gaps-output",
        "reports/model_b_v2_team_alias_gaps.csv",
        "--manual-queue-output",
        "reports/manual_mapping_queue.csv",
        "--coverage-output",
        "reports/dota_universe_coverage_audit.json",
    ]
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
