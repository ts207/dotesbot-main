from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


STRATEGY_DIR = Path(__file__).resolve().parent / "strategies"


@dataclass(frozen=True)
class StrategyContract:
    strategy_kind: str
    version: str
    enabled_paper: bool
    enabled_dry_live: bool
    enabled_real_live: bool
    edge_type: str
    target_horizon: str
    expected_hold_sec: int
    entry_trigger: str
    exit_trigger: str
    primary_metric: str
    secondary_metric: str
    promotion_rule: str
    disable_rule: str
    hold_policy: str = ""
    bounce_target: float | None = None
    timeout_sec: int | None = None

    def signal_kwargs(self, *, include_bounce_fields: bool = True) -> dict[str, Any]:
        data = {
            "edge_type": self.edge_type,
            "target_horizon": self.target_horizon,
            "expected_hold_sec": self.expected_hold_sec,
            "entry_trigger": self.entry_trigger,
            "exit_trigger": self.exit_trigger,
            "primary_metric": self.primary_metric,
            "secondary_metric": self.secondary_metric,
            "promotion_rule": self.promotion_rule,
            "disable_rule": self.disable_rule,
        }
        if include_bounce_fields:
            data["bounce_target"] = self.bounce_target
            data["timeout_sec"] = self.timeout_sec
        return data


def _contract_path(strategy_kind: str) -> Path:
    slug = strategy_kind.lower()
    return STRATEGY_DIR / f"{slug}.yaml"


@lru_cache(maxsize=None)
def get(strategy_kind: str) -> StrategyContract:
    path = _contract_path(strategy_kind)
    if not path.exists():
        raise KeyError(f"unknown strategy contract: {strategy_kind}")
    data = yaml.safe_load(path.read_text()) or {}
    if data.get("strategy_kind") != strategy_kind:
        raise RuntimeError(f"{path} strategy_kind mismatch: {data.get('strategy_kind')!r}")
    return StrategyContract(
        strategy_kind=str(data["strategy_kind"]),
        version=str(data["version"]),
        enabled_paper=bool(data.get("enabled_paper", False)),
        enabled_dry_live=bool(data.get("enabled_dry_live", False)),
        enabled_real_live=bool(data.get("enabled_real_live", False)),
        edge_type=str(data["edge_type"]),
        target_horizon=str(data["target_horizon"]),
        expected_hold_sec=int(data.get("expected_hold_sec", 0)),
        entry_trigger=str(data["entry_trigger"]),
        exit_trigger=str(data["exit_trigger"]),
        primary_metric=str(data["primary_metric"]),
        secondary_metric=str(data.get("secondary_metric", "")),
        promotion_rule=str(data["promotion_rule"]),
        disable_rule=str(data["disable_rule"]),
        hold_policy=str(data.get("hold_policy", "")),
        bounce_target=data.get("bounce_target"),
        timeout_sec=data.get("timeout_sec"),
    )
