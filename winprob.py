#!/usr/bin/env python3
"""Runtime calibrated win-probability model — the bot's `fair` price source.

fair(lead, game_time_sec, elo_diff) = P(the team with this net-worth lead wins),
from a SYMMETRIC logistic fit on 1000 OpenDota pro matches (36k game-states):
    logit = intercept + Σ coef_i · feat_i
    feats = [gk, gk·m, gk/√(m+1), sign·gk², elo/100]   (gk = lead/1000, m = minute)

Trained symmetrically (no radiant bias) so fair(+lead) = 1 − fair(−lead).
Pure-math at runtime (no sklearn) so it's cheap in the hot loop.

Elo handling: elo_diff (backed − opponent team Elo) is CLAMPED to ±ELO_CLAMP and
SHRUNK by ELO_SHRINK before entering the model — the pro-data Elo effect is strong
and these markets are lower-tier (leads thrown more), so we hedge its influence.
When either team's Elo is unknown, elo_diff defaults to 0 (gold-only fair).
"""
import json, math, os, re

_MODEL_PATH = "logs/winprob_model.json"
_ELO_PATH = "logs/team_elo.json"
_ELO_NAME_PATH = "logs/team_elo_by_name.json"

ELO_CLAMP = float(os.getenv("WINPROB_ELO_CLAMP", "150"))   # cap |elo_diff| fed to model
ELO_SHRINK = float(os.getenv("WINPROB_ELO_SHRINK", "0.7")) # shade pro Elo effect
# Draft-H2H predicts well pre-game but its fitted coef is far too strong for the
# bot's mid-game operating point (it would price a 5k lead at 0.17). Clamp + shrink
# hard so it's a nudge, not a wrecking ball. By the 3k-lead gate it's near-redundant.
DRAFT_CLAMP = float(os.getenv("WINPROB_DRAFT_CLAMP", "0.04"))
DRAFT_SHRINK = float(os.getenv("WINPROB_DRAFT_SHRINK", "0.30"))
SLOPE_CLAMP = float(os.getenv("WINPROB_SLOPE_CLAMP", "6000"))  # cap |5-min lead change|
FAIR_FLOOR = float(os.getenv("WINPROB_FAIR_FLOOR", "0.03"))
FAIR_CEIL = float(os.getenv("WINPROB_FAIR_CEIL", "0.97"))

_model = None
_model_mtime = 0.0
_elo = {}
_elo_mtime = 0.0
_elo_name = {}
_elo_name_mtime = 0.0
_SUFFIXES = ("team", "esports", "gaming", "club", "thegame")


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _norm_stripped(s) -> str:
    n = _norm(s)
    for suf in _SUFFIXES:
        if n.endswith(suf) and len(n) > len(suf) + 2:
            n = n[: -len(suf)]
        if n.startswith(suf) and len(n) > len(suf) + 2:
            n = n[len(suf):]
    return n


def _load_model():
    global _model, _model_mtime
    try:
        mt = os.path.getmtime(_MODEL_PATH)
        if _model is None or mt != _model_mtime:
            _model = json.load(open(_MODEL_PATH))
            _model_mtime = mt
    except Exception:
        if _model is None:
            # safe fallback coefficients (symmetric fit) if file missing
            # [gk, gk*m, gk/sqrt(m+1), sign*gk^2, elo/100, slope/1000, draft*10]
            _model = {"coef": [-0.055, 0.0, 1.15, 0.0, 0.70, 0.073, 4.92], "intercept": 0.0}
    return _model


def _load_elo():
    global _elo, _elo_mtime
    try:
        mt = os.path.getmtime(_ELO_PATH)
        if mt != _elo_mtime:
            _elo = {str(k): float(v) for k, v in json.load(open(_ELO_PATH)).items()}
            _elo_mtime = mt
    except Exception:
        pass
    return _elo


def _load_elo_name():
    global _elo_name, _elo_name_mtime
    try:
        mt = os.path.getmtime(_ELO_NAME_PATH)
        if mt != _elo_name_mtime:
            raw = {str(k): float(v) for k, v in json.load(open(_ELO_NAME_PATH)).items()}
            # also index by suffix-stripped name so "BetBoom Team" hits "BetBoom"
            idx = dict(raw)
            for k, v in raw.items():
                idx.setdefault(_norm_stripped(k), v)
            _elo_name = idx
            _elo_name_mtime = mt
    except Exception:
        pass
    return _elo_name


def team_elo(team_id, team_name=None) -> float | None:
    """Resolve a team's Elo: by id first (Steam feed gives it ~3% of the time),
    then by normalized name (the feed almost always has the name). Top pro teams
    are rated; the id-only path missed them when the feed dropped the id."""
    if team_id not in (None, "", "0", 0):
        r = _load_elo().get(str(team_id))
        if r is not None:
            return r
    if team_name:
        nm = _load_elo_name()
        return nm.get(_norm(team_name)) or nm.get(_norm_stripped(team_name))
    return None


def elo_diff(backed_team_id, opp_team_id, backed_name=None, opp_name=None) -> float | None:
    """backed − opponent Elo; None if either is unknown."""
    a = team_elo(backed_team_id, backed_name)
    b = team_elo(opp_team_id, opp_name)
    if a is None or b is None:
        return None
    return a - b


def fair(lead, game_time_sec, elo_diff=None, lead_slope=None, draft_h2h=None) -> float:
    """P(team with `lead` net-worth advantage wins). ALL inputs in the LEADER's
    perspective (lead>0). elo_diff/lead_slope/draft_h2h are leader−opponent and
    default to 0 (neutral) when unknown. lead_slope = leader's net-worth-lead
    change over the trailing window (growing>0); draft_h2h = leader's hero-matchup
    advantage (~±0.1)."""
    m = _load_model()
    minute = max(0.0, float(game_time_sec or 0) / 60.0)
    g = float(lead or 0)
    gk = g / 1000.0
    e = 0.0
    if elo_diff is not None:
        e = max(-ELO_CLAMP, min(ELO_CLAMP, float(elo_diff))) * ELO_SHRINK
    sl = max(-SLOPE_CLAMP, min(SLOPE_CLAMP, float(lead_slope or 0.0)))
    dr = 0.0
    if draft_h2h is not None:
        dr = max(-DRAFT_CLAMP, min(DRAFT_CLAMP, float(draft_h2h))) * DRAFT_SHRINK
    feats = [gk, gk * minute, gk / math.sqrt(minute + 1.0), math.copysign(gk * gk, g),
             e / 100.0, sl / 1000.0, dr * 10.0]
    coef = m["coef"]
    z = float(m.get("intercept", 0.0)) + sum(c * f for c, f in zip(coef, feats))
    # temperature scaling (calibration): T>1 shrinks toward 0.5. Fit on the live
    # game-state population because the raw logistic is overconfident there
    # (said 0.76 where reality was 0.70). See fit_winprob_temp.py.
    T = float(m.get("temperature", 1.0)) or 1.0
    p = 1.0 / (1.0 + math.exp(-z / T))
    return max(FAIR_FLOOR, min(FAIR_CEIL, p))


# ---- draft head-to-head (hero matchup) ----
_MATCHUP_PATH = "logs/opendota_hero_matchups.json"
_matchups = {}
_matchups_mtime = 0.0
_DRAFT_K = 40.0


def _load_matchups():
    global _matchups, _matchups_mtime
    try:
        mt = os.path.getmtime(_MATCHUP_PATH)
        if mt != _matchups_mtime:
            _matchups = json.load(open(_MATCHUP_PATH))
            _matchups_mtime = mt
    except Exception:
        pass
    return _matchups


def _hero_wr(a, b) -> float:
    d = _load_matchups().get(str(a), {}).get(str(b))
    if not d or d[0] < 5:
        return 0.5
    return (d[1] + 0.5 * _DRAFT_K) / (d[0] + _DRAFT_K)


def draft_h2h(my_heroes, opp_heroes):
    """Mean matchup-win-rate advantage of my lineup vs theirs (leader perspective
    when called with the leader's heroes first). None if either lineup is empty."""
    mine = [h for h in (my_heroes or []) if h]
    opp = [h for h in (opp_heroes or []) if h]
    if not mine or not opp:
        return None
    n = len(mine) * len(opp)
    return float(sum(_hero_wr(r, d) - 0.5 for r in mine for d in opp) / n)


if __name__ == "__main__":
    for ld, mn, el, sl, dr in [(2000, 480, None, None, None), (5000, 1200, 100, 800, 0.05),
                               (5000, 1200, 0, -1500, -0.05), (8000, 2100, None, 0, 0)]:
        print(f"lead={ld:>6} min={mn//60:>2} elo={el} slope={sl} draft={dr}  fair={fair(ld, mn, el, sl, dr):.3f}")
    print("elo map teams:", len(_load_elo()), "| matchup heroes:", len(_load_matchups()))
