"""strategy_allocator.py — Explicit strategy candidate collection + allocation.

Replaces implicit runtime priority (event fires → value blocked by _entered_toks)
with a transparent collect → allocate → execute pattern.

Priority order:
  1. EVENT_CONTINUATION_EDGE
  2. MODEL_VALUE_EDGE
  3. VALUE_EDGE
  4. EVENT_REVERSAL_EDGE
  5. DSWING

Per token_id:
  - Token already in entered_tokens → all candidates blocked (already_entered).
  - Multiple candidates on same token → highest-priority wins; others are
    recorded as blocked with a preempted_by_<strategy> reason.
  - Uncontested candidate → winner, empty blocked list.

This module has NO async code, NO executor calls, and NO alpha logic.
It only sorts and filters a pre-built candidate list.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Priority — lower index = higher priority.
_STRATEGY_PRIORITY: dict[str, int] = {
    "EVENT_CONTINUATION_EDGE": 0,
    "MODEL_VALUE_EDGE": 1,
    "VALUE_EDGE": 2,
    "EVENT_REVERSAL_EDGE": 3,
    "DSWING": 4,
}


@dataclass
class StrategyCandidate:
    """A signal from one engine that is eligible to trade a specific token."""
    strategy: str           # EVENT_CONTINUATION_EDGE | EVENT_REVERSAL_EDGE | VALUE_EDGE | DSWING
    token_id: str
    match_id: str
    direction: str          # "radiant" | "dire"
    edge: float
    fair: float
    game_time_sec: int
    signal: Any             # raw signal object (kept for executor use)
    edge_type: str = ""
    target_horizon: str = ""
    expected_hold_sec: int | None = None
    entry_trigger: str = ""
    exit_trigger: str = ""
    primary_metric: str = ""
    secondary_metric: str = ""
    promotion_rule: str = ""
    disable_rule: str = ""
    # VALUE_EDGE only — whether _value_confirmation_passes() returned True
    would_pass_confirmation: bool = True
    is_reversal: bool = False
    # Optional: human-readable event subtype for EVENT strategies
    event_subtype: str = ""


@dataclass
class AllocationDecision:
    """Result of allocating one token_id's candidates."""
    token_id: str
    match_id: str
    winner: StrategyCandidate | None
    blocked: list[StrategyCandidate] = field(default_factory=list)
    # "already_entered" | "preempted_by_event" | "preempted_by_value" | "no_candidates" | ""
    block_reason: str = ""
    counterfactual_note: str = ""


# ---------------------------------------------------------------------------
# Core allocation logic
# ---------------------------------------------------------------------------

def _priority(candidate: StrategyCandidate) -> int:
    return _STRATEGY_PRIORITY.get(candidate.strategy, 99)


def _block_reason_for_winner(winner: StrategyCandidate) -> str:
    strategy_key = winner.strategy.lower().replace("_edge", "").replace("_continuation", "_event").replace("_reversal", "_event")
    # Simplified: just name the winner strategy
    if winner.strategy in ("EVENT_CONTINUATION_EDGE", "EVENT_REVERSAL_EDGE"):
        return "preempted_by_event"
    if winner.strategy == "MODEL_VALUE_EDGE":
        return "preempted_by_model"
    if winner.strategy == "VALUE_EDGE":
        return "preempted_by_value"
    if winner.strategy == "DSWING":
        return "preempted_by_dswing"
    return "preempted"


def allocate_candidates(
    candidates: list[StrategyCandidate],
    entered_tokens: set[str],
    active_model_value_matches: set[str] | None = None,
    active_match_tokens: dict[str, set[str]] | None = None,
) -> list[AllocationDecision]:
    """Allocate a list of candidates to AllocationDecision objects.

    One AllocationDecision is produced per unique token_id that appears in
    candidates. Candidates are grouped by token_id; within each group the
    highest-priority strategy wins and the rest are recorded as blocked.

    Tokens that are already in entered_tokens get block_reason="already_entered"
    and no winner.

    Args:
        candidates: All StrategyCandidate objects collected this tick.
        entered_tokens: Set of token_ids currently held / pending.
        active_model_value_matches: Matches with active MODEL_VALUE_EDGE positions.
        active_match_tokens: Map of match_id to set of active token_ids.

    Returns:
        List of AllocationDecision — one per token_id in candidates.
    """
    active_model_value_matches = active_model_value_matches or set()
    active_match_tokens = active_match_tokens or {}
    newly_allocated_model_value_matches = set()

    # Group candidates by token_id.
    by_token: dict[str, list[StrategyCandidate]] = {}
    for c in candidates:
        by_token.setdefault(c.token_id, []).append(c)

    decisions: list[AllocationDecision] = []

    for token_id, token_candidates in by_token.items():
        # Sort by priority (ascending = highest priority first).
        token_candidates.sort(key=_priority)

        if token_id in entered_tokens:
            decisions.append(AllocationDecision(
                token_id=token_id,
                match_id=token_candidates[0].match_id,
                winner=None,
                blocked=token_candidates,
                block_reason="already_entered",
                counterfactual_note=(
                    f"Token {token_id} already held. "
                    f"Blocked {len(token_candidates)} candidate(s): "
                    f"{[c.strategy for c in token_candidates]}."
                ),
            ))
            continue

        winner = None
        blocked = []

        for c in token_candidates:
            if winner is None:
                if c.strategy == "MODEL_VALUE_EDGE":
                    m_id = c.match_id
                    # Block if match already has model value
                    if m_id in active_model_value_matches or m_id in newly_allocated_model_value_matches:
                        blocked.append(c)
                        continue
                    # Block if match has ANY active token (meaning opposing token since this token is not in entered_tokens)
                    if active_match_tokens.get(m_id):
                        blocked.append(c)
                        continue

                winner = c
                if winner.strategy == "MODEL_VALUE_EDGE":
                    newly_allocated_model_value_matches.add(winner.match_id)
            else:
                blocked.append(c)

        if winner:
            decisions.append(AllocationDecision(
                token_id=token_id,
                match_id=winner.match_id,
                winner=winner,
                blocked=blocked,
                block_reason=_block_reason_for_winner(winner) if blocked else "",
                counterfactual_note=f"Winner: {winner.strategy}. Blocked {len(blocked)} lower-priority." if blocked else ""
            ))
        else:
            decisions.append(AllocationDecision(
                token_id=token_id,
                match_id=token_candidates[0].match_id,
                winner=None,
                blocked=blocked,
                block_reason="match_exposure_blocked",
                counterfactual_note=f"Blocked {len(blocked)} candidates due to match exposure."
            ))

    return decisions


# ---------------------------------------------------------------------------
# Helpers for logging
# ---------------------------------------------------------------------------

def decision_to_log_row(decision: AllocationDecision, *, include_uncontested: bool = True) -> dict | None:
    """Produce a log-dict for allocation decisions.

    Returns None for uncontested winners if include_uncontested is False.
    """
    if not include_uncontested and not decision.blocked and decision.block_reason != "already_entered":
        return None

    winner = decision.winner
    candidate_count = (1 if winner else 0) + len(decision.blocked)
    return {
        "token_id": decision.token_id,
        "match_id": decision.match_id,
        "candidate_count": candidate_count,
        "blocked_count": len(decision.blocked),
        "allocator_winner": winner.strategy if winner else "",
        "winner_strategy": winner.strategy if winner else "",
        "winner_edge": winner.edge if winner else "",
        "winner_fair": winner.fair if winner else "",
        "winner_game_time_sec": winner.game_time_sec if winner else "",
        "winner_direction": winner.direction if winner else "",
        "winner_event_subtype": winner.event_subtype if winner else "",
        "winner_is_reversal": winner.is_reversal if winner else "",
        "winner_edge_type": winner.edge_type if winner else "",
        "winner_target_horizon": winner.target_horizon if winner else "",
        "winner_expected_hold_sec": winner.expected_hold_sec if winner else "",
        "winner_entry_trigger": winner.entry_trigger if winner else "",
        "winner_exit_trigger": winner.exit_trigger if winner else "",
        "winner_primary_metric": winner.primary_metric if winner else "",
        "blocked_strategies": json.dumps([c.strategy for c in decision.blocked]),
        "blocked_edges": json.dumps([round(c.edge, 6) for c in decision.blocked]),
        "blocked_fairs": json.dumps([round(c.fair, 6) for c in decision.blocked]),
        "blocked_edge_types": json.dumps([c.edge_type for c in decision.blocked]),
        "blocked_target_horizons": json.dumps([c.target_horizon for c in decision.blocked]),
        "blocked_expected_hold_secs": json.dumps([c.expected_hold_sec for c in decision.blocked]),
        "block_reason": decision.block_reason,
        "counterfactual_note": decision.counterfactual_note,
    }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from dataclasses import make_dataclass
    FakeSig = make_dataclass("FakeSig", [("token_id", str), ("match_id", str), ("edge", float)])

    c1 = StrategyCandidate("EVENT_CONTINUATION_EDGE", "tok_A", "m1", "radiant", 0.12, 0.83, 900, FakeSig("tok_A", "m1", 0.12))
    c2 = StrategyCandidate("VALUE_EDGE", "tok_A", "m1", "radiant", 0.10, 0.81, 900, FakeSig("tok_A", "m1", 0.10))
    c3 = StrategyCandidate("VALUE_EDGE", "tok_B", "m1", "dire",    0.11, 0.79, 900, FakeSig("tok_B", "m1", 0.11))

    decisions = allocate_candidates([c1, c2, c3], entered_tokens=set())
    for d in decisions:
        print(f"token={d.token_id} winner={d.winner.strategy if d.winner else None} "
              f"blocked={[b.strategy for b in d.blocked]} reason={d.block_reason!r}")
        row = decision_to_log_row(d)
        if row:
            print(f"  log_row={row}")
    print("Smoke test OK.")
