from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Literal, Mapping

import winprob

@dataclass(frozen=True)
class FairValueResult:
    side: str
    fair: float
    elo_diff: float | None
    lead_slope: float | None = None
    draft_h2h: float | None = None
    fair_source: str = "winprob"
    fair_raw: float | None = None
    fair_used: float | None = None
    model_available: bool = True
    model_reason: str = "ok"

# Rolling radiant-lead history per match → net-worth-lead trajectory (slope).
_SLOPE_WINDOW_NS = 300 * 1_000_000_000          # 5-minute window
_lead_hist: dict[str, deque] = {}

def _lead_slope(match_id: str, radiant_lead: int, now_ns: int, record_history: bool = True) -> float:
    """Change in radiant net-worth lead over the trailing ~5 min. 0.0 until enough
    history exists. A growing leader's lead → positive (in radiant perspective)."""
    dq = _lead_hist.setdefault(match_id, deque(maxlen=4000))
    if record_history and (not dq or dq[-1][0] != now_ns):
        dq.append((now_ns, int(radiant_lead)))
    cutoff = now_ns - int(_SLOPE_WINDOW_NS * 1.5)
    while dq and dq[0][0] < cutoff:
        dq.popleft()
    target = now_ns - _SLOPE_WINDOW_NS
    past = None
    for ns, ld in dq:
        if ns <= target:
            past = ld
        else:
            break
    return 0.0 if past is None else float(radiant_lead - past)

def compute_side_fair(
    *,
    game: Mapping[str, Any],
    side: str,  # "radiant" or "dire"
    radiant_lead_override: int | None = None,
    received_at_ns_override: int | None = None,
    include_slope: bool = True,
    include_draft: bool = True,
    record_history: bool = True,
) -> FairValueResult:
    match_id = str(game.get("match_id") or game.get("lobby_id") or "")
    now_ns = received_at_ns_override if received_at_ns_override is not None else int(game.get("received_at_ns") or time.time_ns())

    if side not in {"radiant", "dire"}:
        return FairValueResult(
            side=side,
            fair=0.5,
            elo_diff=None,
            fair_raw=None,
            fair_used=None,
            model_available=False,
            model_reason="unknown_side",
        )

    lead_input = game.get("radiant_lead")
    if radiant_lead_override is None and lead_input in (None, ""):
        return FairValueResult(
            side=side,
            fair=0.5,
            elo_diff=None,
            fair_raw=None,
            fair_used=None,
            model_available=False,
            model_reason="missing_radiant_lead",
        )

    if radiant_lead_override is not None:
        radiant_lead = radiant_lead_override
    else:
        try:
            radiant_lead = int(lead_input or 0)
        except (TypeError, ValueError):
            return FairValueResult(
                side=side,
                fair=0.5,
                elo_diff=None,
                fair_raw=None,
                fair_used=None,
                model_available=False,
                model_reason="invalid_radiant_lead",
            )
        
    rtid, dtid = game.get("radiant_team_id"), game.get("dire_team_id")
    rname, dname = game.get("radiant_team"), game.get("dire_team")
    game_time_input = game.get("game_time_sec")
    if game_time_input in (None, ""):
        return FairValueResult(
            side=side,
            fair=0.5,
            elo_diff=None,
            fair_raw=None,
            fair_used=None,
            model_available=False,
            model_reason="missing_game_time",
        )
    try:
        game_time = int(float(game_time_input))
    except (TypeError, ValueError):
        return FairValueResult(
            side=side,
            fair=0.5,
            elo_diff=None,
            fair_raw=None,
            fair_used=None,
            model_available=False,
            model_reason="invalid_game_time",
        )

    slope_rad = _lead_slope(match_id, radiant_lead, now_ns, record_history) if include_slope else 0.0
    
    draft_rad = None
    if include_draft:
        players = game.get("players") or []
        rad_heroes = [p.get("hero_id") for p in players if p.get("team") == 0]
        dire_heroes = [p.get("hero_id") for p in players if p.get("team") == 1]
        draft_rad = winprob.draft_h2h(rad_heroes, dire_heroes)

    if side == "radiant":
        elo_diff = winprob.elo_diff(rtid, dtid, rname, dname)
        lead_slope = slope_rad
        draft = draft_rad
        side_lead = radiant_lead
    else:
        elo_diff = winprob.elo_diff(dtid, rtid, dname, rname)
        lead_slope = -slope_rad
        draft = (-draft_rad) if draft_rad is not None else None
        side_lead = -radiant_lead

    # Always compute from leader's perspective, then invert if we are behind.
    fair_leader = winprob.fair(abs(side_lead), game_time, elo_diff, lead_slope, draft)
    fair_price = fair_leader if side_lead >= 0 else 1.0 - fair_leader

    return FairValueResult(
        side=side,
        fair=fair_price,
        elo_diff=elo_diff,
        lead_slope=lead_slope,
        draft_h2h=draft,
        fair_raw=fair_price,
        fair_used=fair_price,
        model_available=True,
        model_reason="ok",
    )
