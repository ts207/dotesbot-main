"""strategy_allocator.py — Explicit strategy candidate collection + allocation.

Replaces implicit runtime priority (event fires → value blocked by _entered_toks)
with a transparent collect → allocate → execute pattern.

Priority order:
  1. EVENT_CONTINUATION_EDGE
  2. VALUE_EDGE
  3. EVENT_REVERSAL_EDGE
  4. DSWING

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
    "VALUE_EDGE": 1,
    "EVENT_REVERSAL_EDGE": 2,
    "DSWING": 3,
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
    if winner.strategy == "VALUE_EDGE":
        return "preempted_by_value"
    if winner.strategy == "DSWING":
        return "preempted_by_dswing"
    return "preempted"


def allocate_candidates(
    candidates: list[StrategyCandidate],
    entered_tokens: set[str],
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

    Returns:
        List of AllocationDecision — one per token_id in candidates.
    """
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

        winner = token_candidates[0]
        blocked = token_candidates[1:]
        if blocked:
            block_reason = _block_reason_for_winner(winner)
            note_parts = [f"Winner={winner.strategy}(edge={winner.edge:.4f})"]
            note_parts += [f"{c.strategy}(edge={c.edge:.4f})" for c in blocked]
            counterfactual_note = "Preempted: " + " vs ".join(note_parts)
        else:
            block_reason = ""
            counterfactual_note = ""

        decisions.append(AllocationDecision(
            token_id=token_id,
            match_id=winner.match_id,
            winner=winner,
            blocked=blocked,
            block_reason=block_reason,
            counterfactual_note=counterfactual_note,
        ))

    return decisions


# ---------------------------------------------------------------------------
# Helpers for logging
# ---------------------------------------------------------------------------

def decision_to_log_row(decision: AllocationDecision) -> dict | None:
    """Produce a log-dict for contested/blocked decisions.

    Returns None for uncontested winners (nothing to attribute).
    """
    if not decision.blocked and decision.block_reason != "already_entered":
        return None

    winner = decision.winner
    return {
        "token_id": decision.token_id,
        "match_id": decision.match_id,
        "winner_strategy": winner.strategy if winner else "",
        "winner_edge": winner.edge if winner else "",
        "winner_fair": winner.fair if winner else "",
        "winner_game_time_sec": winner.game_time_sec if winner else "",
        "winner_direction": winner.direction if winner else "",
        "winner_event_subtype": winner.event_subtype if winner else "",
        "winner_is_reversal": winner.is_reversal if winner else "",
        "blocked_strategies": json.dumps([c.strategy for c in decision.blocked]),
        "blocked_edges": json.dumps([round(c.edge, 6) for c in decision.blocked]),
        "blocked_fairs": json.dumps([round(c.fair, 6) for c in decision.blocked]),
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
