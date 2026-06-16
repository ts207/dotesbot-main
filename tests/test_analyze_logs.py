from __future__ import annotations

import csv

from analyze_logs import compute_paper_splits


def test_compute_paper_splits(tmp_path):
    path = tmp_path / "paper_trades.csv"
    headers = [
        "action",
        "pnl_usd",
        "would_pass_live",
        "policy_allowed",
        "paper_only_bypass",
        "live_skip_reason",
        "policy_reason",
        "risk_tags",
    ]
    rows = [
        {
            "action": "exit",
            "pnl_usd": "3.5",
            "would_pass_live": "true",
            "policy_allowed": "true",
            "paper_only_bypass": "false",
        },
        {
            "action": "exit",
            "pnl_usd": "-1.0",
            "would_pass_live": "false",
            "policy_allowed": "false",
            "paper_only_bypass": "true",
            "live_skip_reason": "book_stale:age_ms=999",
            "risk_tags": "book_stale",
        },
        {
            "action": "exit",
            "pnl_usd": "2.0",
            "would_pass_live": "false",
            "policy_allowed": "false",
            "paper_only_bypass": "true",
            "policy_reason": "mapping_invalid",
            "risk_tags": "mapping_valid",
        },
        {"action": "entry", "pnl_usd": "100"},
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

    splits = compute_paper_splits(str(path))

    assert splits.all_paper_pnl == 4.5
    assert splits.live_parity_paper_pnl == 3.5
    assert splits.paper_only_pnl == 1.0
    assert splits.stale_book_pnl == -1.0
    assert splits.mapping_risk_pnl == 2.0
