#!/usr/bin/env python3
"""Render the survival strategy audit report."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from survival_strategy import build_report


REPORTS = REPO_ROOT / "reports"


def build_markdown(report: dict[str, Any]) -> str:
    ev = report["evidence"]
    value_nc = ev.get("value_settlement_backtest", {}).get("no_confirmation", {})
    ds_sweep = ev.get("decisive_swing_ml_sniper_backtest", {}).get("sweep", [])
    ds_best = ds_sweep[0] if ds_sweep else {}
    structure_totals = ev.get("gettoplive_structure_state_audit", {})
    survival_features = ev.get("value_survival_feature_audit", {})
    survival_rec = survival_features.get("recommendation", {})
    lines = [
        "# Survival Strategy Report",
        "",
        f"Generated: {report['generated_at']}",
        f"Profile: `{report['profile_id']}`",
        f"Decision: **{report['decision']}**",
        "",
        "## Strategy",
        "",
        "Primary: VALUE hold-to-settlement on GetTopLive top_live snapshots. Back the net-worth leader only when fair-price edge survives the validated gates.",
        "",
        "Secondary: DSWING BO3 moneyline convergence after a decisive map swing. Keep off by default because the sample is small and exit liquidity is thin.",
        "",
        "Rejected branch: Model B residual/pregame feature trading. Current checked-in diagnosis says B0 market-only remains best and residual variants failed source-robust validation.",
        "",
        "## Evidence",
        "",
        f"VALUE no-confirmation replay: trades={value_nc.get('trades')} wins={value_nc.get('wins')} losses={value_nc.get('losses')} roi={value_nc.get('roi_pct')}% pnl=${value_nc.get('pnl_usd')}.",
        f"DSWING best checked sweep: lead>={ds_best.get('lead_threshold')} trades={ds_best.get('trades')} win_pct={ds_best.get('win_pct')}% roi={ds_best.get('roi_pct')}%.",
        f"GetTopLive structure audit: rows={structure_totals.get('top_live_rows')} matches={structure_totals.get('top_live_matches')} valid_tower_deltas={structure_totals.get('valid_tower_deltas')} building_only_changes={structure_totals.get('building_change_without_tower_change')} tower_count_increases={structure_totals.get('tower_count_increases')}.",
        f"VALUE survival feature audit: join_coverage={survival_features.get('join_coverage_pct')}% live_gate_change={survival_rec.get('live_gate_change')} candidate_observations={len(survival_rec.get('candidate_observations', []))}.",
        "",
        "## GetTopLive State Audit",
        "",
    ]
    source = report.get("source_state", {})
    lines += [
        f"Status: `{source.get('status')}`",
        f"Recent rows read: {source.get('rows_read')} from `{source.get('csv_path')}`",
        f"Required source `{source.get('required_data_source')}` rows: {source.get('required_source_rows')} across {source.get('required_source_unique_matches')} matches.",
        f"Source counts: {source.get('source_counts')}",
        f"Missing required fields: {source.get('missing_required_fields')}",
        f"Missing recommended fields: {source.get('missing_recommended_fields')}",
        "",
        "## Structure State Policy",
        "",
    ]
    structure_policy = report.get("structure_state_policy", {})
    if structure_policy:
        lines += [
            f"- TopLive building_state: {structure_policy.get('top_live_building_state')}",
            f"- TopLive tower_state: {structure_policy.get('top_live_tower_state')}",
            f"- TopLive rax/T4/base: {structure_policy.get('top_live_rax_and_t4')}",
            f"- Runtime marker: `{structure_policy.get('runtime_schema_marker')}`",
            f"- Survival rule: {structure_policy.get('survival_rule')}",
            "",
        ]
    lines += [
        "## Current Config Audit",
        "",
    ]
    for item in report["findings"]:
        lines.append(f"- {item['level'].upper()} {item['code']}: {item['detail']}")
    lines += [
        "",
        "## Go-Live Rules",
        "",
        "- Do not enable real trading from this report alone.",
        "- Before real capital: stable balance reads, no stale no-book position, bot-only commitment, and caps scaled to bankroll.",
        "- Keep VALUE confirmation off unless a fresh replay proves otherwise.",
        "- Keep Model B out of trading until it passes source-robust validation.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    report = build_report()
    REPORTS.mkdir(exist_ok=True)
    json_path = REPORTS / "survival_strategy_report.json"
    md_path = REPORTS / "survival_strategy_report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(build_markdown(report), encoding="utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    print(f"decision={report['decision']}")
    return 0 if report["decision"] != "NO_GO" else 1


if __name__ == "__main__":
    sys.exit(main())
