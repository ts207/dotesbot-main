from __future__ import annotations

import csv
import os
from dataclasses import dataclass

from config import PAPER_TRADES_CSV_PATH


@dataclass
class PaperSplits:
    all_paper_pnl: float = 0.0
    live_parity_paper_pnl: float = 0.0
    paper_only_pnl: float = 0.0
    stale_book_pnl: float = 0.0
    stale_steam_pnl: float = 0.0
    wide_spread_pnl: float = 0.0
    mapping_risk_pnl: float = 0.0
    trades: int = 0
    live_parity_trades: int = 0
    paper_only_trades: int = 0


def _bool(value) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _float(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def compute_paper_splits(path: str = PAPER_TRADES_CSV_PATH) -> PaperSplits:
    splits = PaperSplits()
    if not os.path.exists(path):
        return splits

    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if str(row.get("action") or "").lower() != "exit":
                continue
            pnl = _float(row.get("pnl_usd"))
            reason = " ".join(
                str(row.get(key) or "").lower()
                for key in ("live_skip_reason", "policy_reason", "risk_tags")
            )
            would_pass_live = _bool(row.get("would_pass_live"))
            policy_allowed = _bool(row.get("policy_allowed"))
            paper_only = bool(_bool(row.get("paper_only_bypass")))
            live_parity = (
                would_pass_live is not False
                and policy_allowed is not False
                and not paper_only
            )

            splits.trades += 1
            splits.all_paper_pnl += pnl
            if live_parity:
                splits.live_parity_trades += 1
                splits.live_parity_paper_pnl += pnl
            else:
                splits.paper_only_trades += 1
                splits.paper_only_pnl += pnl

            if "book_stale" in reason or "stale_book" in reason:
                splits.stale_book_pnl += pnl
            if "steam_stale" in reason or "stale_steam" in reason:
                splits.stale_steam_pnl += pnl
            if "spread_too_wide" in reason or "wide_spread" in reason:
                splits.wide_spread_pnl += pnl
            if "mapping" in reason or "orientation_flip" in reason:
                splits.mapping_risk_pnl += pnl
    return splits


def main() -> int:
    splits = compute_paper_splits()
    print(f"all_paper_pnl={splits.all_paper_pnl:.4f} trades={splits.trades}")
    print(f"live_parity_paper_pnl={splits.live_parity_paper_pnl:.4f} trades={splits.live_parity_trades}")
    print(f"paper_only_pnl={splits.paper_only_pnl:.4f} trades={splits.paper_only_trades}")
    print(f"stale_book_pnl={splits.stale_book_pnl:.4f}")
    print(f"stale_steam_pnl={splits.stale_steam_pnl:.4f}")
    print(f"wide_spread_pnl={splits.wide_spread_pnl:.4f}")
    print(f"mapping_risk_pnl={splits.mapping_risk_pnl:.4f}")
    if splits.all_paper_pnl > 0 and splits.live_parity_paper_pnl <= 0:
        print("deployable=false reason=live_parity_subset_not_profitable")
        return 1
    print("deployable=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
