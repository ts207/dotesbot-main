from __future__ import annotations

import os
import json
import hashlib
import subprocess
import time
from event_taxonomy import RETIRED_FIXED_WINDOW_EVENTS, TIER_A_EVENTS, TIER_B_EVENTS, UNREACHABLE_PRO_EVENTS

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        return False

load_dotenv()

RUN_ID = os.getenv("RUN_ID") or str(int(time.time()))
DOTA_FAIR_MODEL_PATH = os.getenv("DOTA_FAIR_MODEL_PATH", "dota_fair_model/models/dota_fair.joblib")


def _git_code_version() -> str:
    env_version = os.getenv("CODE_VERSION")
    if env_version:
        return env_version
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(__file__),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


CODE_VERSION = _git_code_version()

STEAM_API_KEY = os.getenv("STEAM_API_KEY")
MODE = os.getenv("MODE", "paper").lower()

# Guarded live-path switch. Defaults to false; paper mode remains the default.
LIVE_TRADING = os.getenv("LIVE_TRADING", "false").lower() in {"1", "true", "yes"}
ENABLE_REAL_LIVE_TRADING = os.getenv("ENABLE_REAL_LIVE_TRADING", "false").lower() in {"1", "true", "yes"}
MAX_TOTAL_LIVE_USD = float(os.getenv("MAX_TOTAL_LIVE_USD", "1000"))
MAX_DAILY_DRAWDOWN_USD = float(os.getenv("MAX_DAILY_DRAWDOWN_USD", "25"))
MAX_TRADE_USD = float(os.getenv("MAX_TRADE_USD", "1"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "1"))
LIVE_ORDER_TYPE = os.getenv("ORDER_TYPE", "FAK").upper()
LIVE_TICK_SIZE = os.getenv("LIVE_TICK_SIZE", "0.01")
LIVE_SAFETY_MARGIN = float(os.getenv("LIVE_SAFETY_MARGIN", "0.02"))
LIVE_REQUIRE_CADENCE_SCHEMA = os.getenv("LIVE_REQUIRE_CADENCE_SCHEMA", "true").lower() in {"1", "true", "yes"}
LIVE_ALLOWED_CADENCE_QUALITIES = {
    q.strip() for q in os.getenv("LIVE_ALLOWED_CADENCE_QUALITIES", "direct,normal").split(",") if q.strip()
}
LIVE_MIN_EVENT_QUALITY = float(os.getenv("LIVE_MIN_EVENT_QUALITY", "0.60"))
LIVE_MIN_DECISIVE_STOMP_QUALITY = float(os.getenv("LIVE_MIN_DECISIVE_STOMP_QUALITY", "0.35"))
ALLOW_GAME_OVER_ONLY = os.getenv("ALLOW_GAME_OVER_ONLY", "false").lower() in {"1", "true", "yes"}
ALLOW_EVENT_TRADES = os.getenv("ALLOW_EVENT_TRADES", "true").lower() in {"1", "true", "yes"}
EVENT_DETECTORS_ENABLED = os.getenv("EVENT_DETECTORS_ENABLED", "true").lower() in {"1", "true", "yes"}
DISABLE_STRUCTURE_TRADES = os.getenv("DISABLE_STRUCTURE_TRADES", "false").lower() in {"1", "true", "yes"}
# Default live allowlist = current TIER_A + TIER_B only. Delayed Roshan/Aegis
# labels can annotate or veto, but they are not TopLive primitive facts and must
# not open trades.
DEFAULT_TRADE_EVENTS = ",".join(sorted(
    TIER_A_EVENTS | TIER_B_EVENTS
))
TRADE_EVENTS = {e.strip() for e in os.getenv("TRADE_EVENTS", DEFAULT_TRADE_EVENTS).split(",") if e.strip()}
ALLOW_CONFIRMATION_ONLY_LIVE_TRADES = os.getenv("ALLOW_CONFIRMATION_ONLY_LIVE_TRADES", "false").lower() in {"1", "true", "yes"}
LIVE_ATTEMPTS_CSV_PATH = os.getenv("LIVE_ATTEMPTS_CSV_PATH", "logs/live_attempts.csv")
PAPER_ATTEMPTS_CSV_PATH = os.getenv("PAPER_ATTEMPTS_CSV_PATH", "logs/paper_attempts.csv")
PAPER_EXITS_CSV_PATH = os.getenv("PAPER_EXITS_CSV_PATH", "logs/paper_exits.csv")
PAPER_POSITIONS_PATH = os.getenv("PAPER_POSITIONS_PATH", "logs/paper_positions_v2.json")
ACTUAL_DOTA_EVENTS_CSV_PATH = os.getenv("ACTUAL_DOTA_EVENTS_CSV_PATH", "logs/actual_dota_events.csv")
LEGACY_DOTA_EVENTS_CSV_PATH = os.getenv("LEGACY_DOTA_EVENTS_CSV_PATH", "logs/legacy_dota_events.csv")
STRATEGY_SIGNALS_CSV_PATH = os.getenv("STRATEGY_SIGNALS_CSV_PATH", "logs/strategy_signals.csv")

EVENT_TRIGGERED_VALUE_ENABLED = os.getenv("EVENT_TRIGGERED_VALUE_ENABLED", "true").lower() in {"1", "true", "yes"}
ENABLE_EVENT_TRIGGERED_VALUE_TRADING = os.getenv("ENABLE_EVENT_TRIGGERED_VALUE_TRADING", "true").lower() in {"1", "true", "yes"}
EVENT_VALUE_MIN_EDGE = float(os.getenv("EVENT_VALUE_MIN_EDGE", "0.10"))
EVENT_VALUE_MIN_FAIR_DELTA = float(os.getenv("EVENT_VALUE_MIN_FAIR_DELTA", "0.06"))
EVENT_VALUE_MAX_ASK = float(os.getenv("EVENT_VALUE_MAX_ASK", "0.84"))
EVENT_VALUE_MIN_ASK = float(os.getenv("EVENT_VALUE_MIN_ASK", "0.50"))
EVENT_VALUE_MAX_EDGE = float(os.getenv("EVENT_VALUE_MAX_EDGE", "0.30"))
EVENT_VALUE_TRADE_USD = float(os.getenv("EVENT_VALUE_TRADE_USD", "5.0"))
EVENT_VALUE_MIN_GAME_TIME = int(os.getenv("EVENT_VALUE_MIN_GAME_TIME", "600"))
EVENT_VALUE_MAX_GAME_TIME = int(os.getenv("EVENT_VALUE_MAX_GAME_TIME", "1800"))

EVENT_VALUE_REVERSAL_MIN_EDGE = float(os.getenv("EVENT_VALUE_REVERSAL_MIN_EDGE", "0.14"))
EVENT_VALUE_REVERSAL_MIN_FAIR_DELTA = float(os.getenv("EVENT_VALUE_REVERSAL_MIN_FAIR_DELTA", "0.10"))
EVENT_VALUE_REVERSAL_MAX_ASK = float(os.getenv("EVENT_VALUE_REVERSAL_MAX_ASK", "0.45"))
EVENT_VALUE_REVERSAL_MIN_ASK = float(os.getenv("EVENT_VALUE_REVERSAL_MIN_ASK", "0.08"))

ENABLE_MATCH_WINNER_GAME3_PROXY = os.getenv(
    "ENABLE_MATCH_WINNER_GAME3_PROXY", "true"
).lower() in {"1", "true", "yes"}

ENABLE_MATCH_WINNER_RESEARCH = os.getenv(
    "ENABLE_MATCH_WINNER_RESEARCH", "false"
).lower() in {"1", "true", "yes"}

ENABLE_MATCH_WINNER_TRADING = os.getenv(
    "ENABLE_MATCH_WINNER_TRADING", "false"
).lower() in {"1", "true", "yes"}

ENABLE_LEGACY_ADVERSE_EXITS = os.getenv(
    "ENABLE_LEGACY_ADVERSE_EXITS", "false"
).lower() in {"1", "true", "yes"}

# Polling / reconnect
# NOTE: GetLiveLeagueGames carries a ~120s Valve-imposed broadcast delay.
# GetTopLiveGame is ~15–30s.
# If market lag is ~60s and Steam delay is ~30s, the actual capture window is ~30s.
STEAM_POLL_SECONDS = float(os.getenv("STEAM_POLL_SECONDS", "3.0"))
WS_RECONNECT_SECONDS = float(os.getenv("WS_RECONNECT_SECONDS", "5"))
# GetLiveLeagueGames refresh interval — it adds ~120s on top of broadcaster delay
# and is only used for team-name enrichment, so polling it slowly is correct.
LLG_REFRESH_SECONDS = int(os.getenv("LLG_REFRESH_SECONDS", "60"))

# Safety thresholds
# 2026-05-28 — Raised 1500→10000. The per-match GetTopLiveGame polling cadence
# has a median snapshot-to-snapshot gap of 16.4s (p99 56s), so a 1.5s gate was
# structurally incompatible: it blocked 48% of signals on `steam_stale` while
# observed steam_age on those skips was median 3.4s, p90 8.2s — all legitimate
# fresh snapshots, just paced by Steam's bucket cadence. The real staleness
# signal is MAX_SOURCE_UPDATE_AGE_SEC (server-side data freshness) below.
# 2026-05-30 — Raised 10000→25000. Today's signal funnel showed steam_stale=96
# (49%) at 10s gate while live inter-snapshot p90=21.6s, p99=64s; 25s covers
# >90% of legitimate snapshots. Hold-to-settle events tolerate the small extra
# age tradeoff (60s markout already absorbs much larger lag).
MAX_STEAM_AGE_MS = int(os.getenv("MAX_STEAM_AGE_MS", "25000"))
# Source freshness guards. received_at_ns only proves the HTTP response is fresh;
# these guards prevent paper trades from slower/stale Dota sources.
# stream_delay_s is spectator/broadcast delay metadata only; it is logged, not used as a skip guard.
REQUIRE_TOP_LIVE_FOR_SIGNALS = os.getenv("REQUIRE_TOP_LIVE_FOR_SIGNALS", "true").lower() in {"1", "true", "yes"}
# 2026-05-28 — Tightened 120→30s. Observed source_update_age_sec distribution
# (top_live only): median 3.8s, p75 38.8s, p90 68.6s, p99 111.3s. The 120s
# threshold sat past p99 and never fired. 30s catches the actual stale tail
# while preserving the p75 of normal traffic.
MAX_SOURCE_UPDATE_AGE_SEC = float(os.getenv("MAX_SOURCE_UPDATE_AGE_SEC", "30"))
MAX_BOOK_AGE_MS = int(os.getenv("MAX_BOOK_AGE_MS", "90000"))
# Signal edge / lag knobs. MIN_EDGE was the old combined knob; keep it as
# a backward-compatible default for MIN_LAG only when MIN_LAG is unset.
MIN_LAG = float(os.getenv("MIN_LAG", os.getenv("MIN_EDGE", "0.05")))
MIN_EXECUTABLE_EDGE = float(os.getenv("MIN_EXECUTABLE_EDGE", "0.05"))
UNDERDOG_REVERSAL_MIN_EDGE = float(os.getenv("UNDERDOG_REVERSAL_MIN_EDGE", "0.02"))
UNDERDOG_REVERSAL_MIN_LAG = float(os.getenv("UNDERDOG_REVERSAL_MIN_LAG", "0.03"))
MIN_ML_EDGE = float(os.getenv("MIN_ML_EDGE", "0.10"))

# --- S3: net-worth value gate (2026-06-03) ---------------------------------
# The only robust edge (806-signal analysis + game logic): net worth predicts
# the winner, and profit = gap between net-worth-implied fair and market price.
# For hold-to-settle winner events, require: backed side leads >= S3_MIN_NW_LEAD
# gold AND calibrated_fair(lead) - price >= S3_MIN_EDGE, price <= S3_MAX_PRICE.
# Makes the trade independent of WHICH detector fired — the NW gate filters.
S3_ENABLED = os.getenv("S3_ENABLED", "true").lower() == "true"
S3_MIN_NW_LEAD = int(os.getenv("S3_MIN_NW_LEAD", "2000"))   # backed side must lead >= this
S3_MIN_EDGE = float(os.getenv("S3_MIN_EDGE", "0.10"))       # calibrated_fair - price floor
S3_MAX_PRICE = float(os.getenv("S3_MAX_PRICE", "0.84"))     # never pay above this (crumbs)
# Elo gate: weak teams throw leads on the RAW swing (58% vs 82%), BUT on the
# value-gated S3 set it didn't help at any margin (only trimmed winners, n=23) —
# the value gate already captures it. DEFAULT OFF; flip S3_ELO_ENABLED=true to test
# once more data accumulates. Fail-open if Elo unknown.
S3_ELO_ENABLED = os.getenv("S3_ELO_ENABLED", "false").lower() == "true"
S3_ELO_MARGIN = int(os.getenv("S3_ELO_MARGIN", "50"))      # skip if backed_elo < opp_elo - this
ML_STRATEGY_ENABLED = os.getenv("ML_STRATEGY_ENABLED", "false").lower() in {"1", "true", "yes"}
PRICE_LOOKBACK_SEC = float(os.getenv("PRICE_LOOKBACK_SEC", "10"))
DEFAULT_MAX_FILL_PRICE = float(os.getenv("DEFAULT_MAX_FILL_PRICE", "0.80"))
MAX_SPREAD = float(os.getenv("MAX_SPREAD", "0.15"))
# 2026-05-30 — Raised 0.08→0.50. The 8c cap was designed for short-horizon
# (30-60s markout) events where spread = realized exit slippage. 11/12 current
# tradable events are hold-to-settle (EXIT_HORIZON=0), so spread on entry is
# absorbed by the binary $0/$1 settlement, not the round-trip. Per-event
# _EVENT_MAX_FILL (0.82-0.94) is the real ceiling. Today: 100% rejected at 0.08
# because tier-2 books run 0.82-0.89 spread.
MAX_VOLATILITY_SPREAD = float(os.getenv("MAX_VOLATILITY_SPREAD", "0.50"))
MAX_MOMENTUM_CHASE = float(os.getenv("MAX_MOMENTUM_CHASE", "0.5"))
MAKER_EXIT_MODE = os.getenv("MAKER_EXIT_MODE", "false").lower() in {"1", "true", "yes"}
BOOK_MOVE_GRACE_SEC = float(os.getenv("BOOK_MOVE_GRACE_SEC", "2.5"))
BOOK_MOVE_ALPHA_THRESHOLD = float(os.getenv("BOOK_MOVE_ALPHA_THRESHOLD", "0.03"))
# POLL_FIGHT_SWING game-time window: early fights have noise, late fights have no edge.
# Min=1200 (20 min): PF=2.41 vs PF=1.44 all-game; early game (0-20 min) adds 24 bad trades.
# Max=2400 (40 min): past 40 min directionless volatility dominates.
# 2026-05-30 — Lowered 1200→900 (15 min). 7d data shows 26/66 fires outside the
# 20-40m window; the 15-20m bucket is the partial compromise between PF and
# throughput. Monitor settle PnL — if it drops below the 2.41 baseline, revert.
# 2026-05-30 — 900→600→720 (15→10→12 min). Settled at 12 min: pre-12 fights
# are noisy/lane-skirmish-tier, post-12 is when real teamfight dynamics kick in.
FIGHT_SWING_MIN_GAME_TIME_SEC = int(os.getenv("FIGHT_SWING_MIN_GAME_TIME_SEC", "720"))
FIGHT_SWING_MAX_GAME_TIME_SEC = int(os.getenv("FIGHT_SWING_MAX_GAME_TIME_SEC", "2400"))
MIN_ASK_SIZE_USD = float(os.getenv("MIN_ASK_SIZE_USD", "25"))
PAPER_TRADE_SIZE_USD = float(os.getenv("PAPER_TRADE_SIZE_USD", "25"))
PAPER_SLIPPAGE_CENTS = float(os.getenv("PAPER_SLIPPAGE_CENTS", "0.01"))
PAPER_EXECUTION_DELAY_MS = int(os.getenv("PAPER_EXECUTION_DELAY_MS", "0"))
# Hard cap on total open paper exposure per match (USD). Prevents runaway stacking
# when multiple events fire in the same direction within a single game.
MAX_OPEN_USD_PER_MATCH = float(os.getenv("MAX_OPEN_USD_PER_MATCH", "150"))
# Prevent immediate re-entry churn after a paper exit in the same token.
PAPER_REENTRY_COOLDOWN_SEC = float(os.getenv("PAPER_REENTRY_COOLDOWN_SEC", "300"))

# Exit thresholds
EXIT_TAKE_PROFIT   = float(os.getenv("EXIT_TAKE_PROFIT",    "0.95"))   # absolute max TP (game-over)
EXIT_STOP_LOSS_ABS = float(os.getenv("EXIT_STOP_LOSS_ABS",  "0.05"))   # floor price
EXIT_STOP_LOSS_REL = float(os.getenv("EXIT_STOP_LOSS_REL",  "0.10"))   # max loss from entry; capped at expected_move
# Trailing stop: exit if bid drops this many cents from its peak since entry.
# Only activates after TRAILING_STOP_GRACE_SEC seconds to avoid noise-triggers on entry dip.
# Set to 0 to disable. Data: mid-game FIGHT_SWING peak-to-trough rarely exceeds 3c in winners.
EXIT_TRAILING_STOP_CENTS = float(os.getenv("EXIT_TRAILING_STOP_CENTS", "0.10"))  # 2026-05-27: was 0.03 (too tight)
EXIT_TRAILING_STOP_GRACE_SEC = float(os.getenv("EXIT_TRAILING_STOP_GRACE_SEC", "30.0"))  # 2026-05-27: was 10 (arm later, less noise)
VALUE_EXIT_FAIR_INVALIDATION_ENABLED = os.getenv("VALUE_EXIT_FAIR_INVALIDATION_ENABLED", "true").lower() in {"1", "true", "yes"}
VALUE_EXIT_FAIR_ENTRY_BUFFER = float(os.getenv("VALUE_EXIT_FAIR_ENTRY_BUFFER", "0.03"))
VALUE_EXIT_FAIR_BID_BUFFER = float(os.getenv("VALUE_EXIT_FAIR_BID_BUFFER", "0.05"))
# If the average market-latency edge window passes before price reaches model
# value, close and stop waiting for the original stale edge to materialize.
# Disabled (0): was forcing exits at 30s before the move completed on 4/7 sample trades.
EXIT_LATENCY_EDGE_SEC = int(os.getenv("EXIT_LATENCY_EDGE_SEC", "0"))
# Time-based exit: per-event horizons calibrated to each event's repricing speed.
# Fallback EXIT_HORIZON_SEC applies for unknown event types or when 0 (disabled).
EXIT_HORIZON_SEC   = int(os.getenv("EXIT_HORIZON_SEC",      "120"))
EXIT_HORIZON_BY_EVENT: dict[str, int] = {
    # 2026-06-02 — RECONCILED positions hold to settle. A restart reconciles open
    # positions with event_type="STARTUP_RECONCILE"; without this entry it fell to
    # the EXIT_HORIZON_SEC default (120s) → hold_to_settle=False → the exit engine
    # actively SOLD it (this dumped the first live LGD position via model_value_exit
    # at 0.68). Reconciled positions are OUR hold-to-settle positions — keep them held.
    "STARTUP_RECONCILE": 0,
    "RUNTIME_RECONCILE": 0,
    "VALUE": 0,
    "EVENT_TRIGGERED_VALUE": 0,
    "DSWING": 0,
    # Strongest signals: hold to Settlement (disabled fixed horizon)
    "THRONE_EXPOSED": 0,
    "OBJECTIVE_CONVERSION_T4": 0,
    "OBJECTIVE_CONVERSION_RAX": 0,
    "POLL_BUYBACK_CAPITULATION": 0,

    # 2026-05-29 settlement audit (67 shadow buys May 15-29):
    # Both POLL_FIGHT_SWING and POLL_ULTRA_LATE_FIGHT_FLIP flip from negative @
    # 60s markout to positive @ settle (FIGHT_SWING 82% win/+$4.57, ULTRA_LATE
    # 100%/+$2.03). The earlier 30s caps were left over from a markout-only
    # validation that ignored settlement payoff. Hold to settle.
    "POLL_ULTRA_LATE_FIGHT_FLIP": 0,
    # 2026-05-30 — switched 30→0 (hold to settle). The 05-27 "peak at 30s" audit
    # was looking at markout, not settle. signal_markouts.csv (n=13): 30s markout
    # +1.83c/share = +$1.08/trade. Settle wr 100% (8/8 trade-eligible, 13/13 full
    # events) at cap 0.85 = +$8.85/trade. Settle is 8x better in expectation,
    # though small-sample risk applies. Reverting if settle wr drops below 80%.
    "POLL_LATE_FIGHT_FLIP": 0,
    "POLL_FIGHT_SWING": 0,
    # Comeback/reversal events: hold to settlement — 30s exits capture none of the alpha
    "POLL_MAJOR_COMEBACK_RECOVERY": 0,
    "POLL_MAJOR_COMEBACK_FADE": 0,  # 2026-05-30 fade signal — hold to settle
    "POLL_NW_KILL_DIVERGENCE": 0,   # 2026-05-30 #6 — hold to settle
    "MANUAL": 0,                    # 2026-05-30 manual UI trades: hold to settle
                                     # (bot must not second-guess operator entries)
    # 2026-05-30 Phase B — all real-time-edge detectors hold to settle
    "POLL_KILL_BURST_TIGHT": 0,
    "POLL_NW_VELOCITY_SUSTAINED": 0,
    "POLL_KILL_GAP_ACCEL": 0,
    "POLL_PHASE_NORMALIZED_LEAD": 0,
    # B4 backtest (validations/backtest_2026_05_25_summary.md): n=19, mean@30s=−0.257
    # win=16% but mean@settle=+1.782 win=75% — alpha is fully captured at settlement.
    "POLL_VALUE_DISAGREEMENT": 0,
    # POLL_STRUCTURAL_DOMINANCE: fires when game is decided (structure +
    # networth + kills all align). Hold to settle to catch the drift toward 1.0
    # as GG consensus forms.
    "POLL_STRUCTURAL_DOMINANCE": 0,

    # Default signals: fixed-horizon tactical exits
    # POLL_FIGHT_SWING horizon → 0 (hold to settle). B4 backtest n=33:
    # mean@30s=+0.121 win=52% vs mean@settle=+1.311 win=92%. Settlement
    # captures roughly 10× the per-trade PnL with a much higher win rate.
    "POLL_TEAM_WIPE": 60,
    # Promoted to TIER_B 2026-05-26 — signal-quality audit shows n=20 +2.25c 75% win.
    # Hold-to-settle (0) per RANK 5 finding: +3.4c at nw_delta>=2000 with 82% win.
    "POLL_COMEBACK_RECOVERY": 0,
    # Promoted to TIER_B 2026-05-26 — n=60 +2.09c 70% win; +9.86c on big-move subset.
    # Hold-to-settle; reprice is sustained.
    "POLL_KILL_BURST_CONFIRMED": 0,
    # 2026-05-29 settlement audit — all 3 stomp events flip positive at settle
    # despite negative @30s/60s. Promoted from RESEARCH (per event_taxonomy.py
    # change) and given hold-to-settle. Sample sizes for ULTRA_LATE and
    # KILL_BURST are small but consistent with the pattern.
    "POLL_LEAD_FLIP_WITH_KILLS": 0,       # was 120 — 8 trades, 75% win, +$4.80 at settle
    "POLL_RAPID_STOMP": 0,                # new — 18 trades, 83% win, +$5.52 at settle
    "POLL_DECISIVE_STOMP": 0,             # new — 8 trades, 88% win, +$2.00 at settle
    "POLL_PRE_PUSH_SETUP": 0,             # 2026-05-29 — new detector, 91% settle win on n=375
    "POLL_FIRST_SWING_SETTLE": 0,        # 2026-05-31 — 80% match wr, hold to settle
    "POLL_REVERSAL_ENTRY": 0,            # 2026-05-31 — 100% wr on 10 matches, hold to settle
    "OBJECTIVE_CONVERSION_T3": 60,
    "BASE_PRESSURE_T4": 60,
    "BASE_PRESSURE_T3_COLLAPSE": 60,
}
# Safety net: force-close any position that stays open longer than this (game_over missed).
MAX_HOLD_HOURS     = float(os.getenv("MAX_HOLD_HOURS",      "4"))

# Underdog reversal: buy cheap comeback tokens (ask ≤ 0.45) with relaxed entry filters.
# Hold to game-over or TP=0.75; exit early only if Aegis goes to the leading team.
UNDERDOG_REVERSAL_EVENTS = {
    e.strip() for e in os.getenv(
        "UNDERDOG_REVERSAL_EVENTS",
        "POLL_COMEBACK_RECOVERY,POLL_MAJOR_COMEBACK_RECOVERY,"
        "POLL_LEAD_FLIP_WITH_KILLS,POLL_FIGHT_SWING",
    ).split(",") if e.strip()
}
UNDERDOG_REVERSAL_MAX_ENTRY   = float(os.getenv("UNDERDOG_REVERSAL_MAX_ENTRY",   "0.45"))
UNDERDOG_REVERSAL_MIN_ENTRY   = float(os.getenv("UNDERDOG_REVERSAL_MIN_ENTRY",   "0.08"))
UNDERDOG_REVERSAL_TAKE_PROFIT = float(os.getenv("UNDERDOG_REVERSAL_TAKE_PROFIT", "0.75"))
UNDERDOG_REVERSAL_STOP_ABS    = float(os.getenv("UNDERDOG_REVERSAL_STOP_ABS",    "0.04"))
UNDERDOG_REVERSAL_MIN_EDGE    = float(os.getenv("UNDERDOG_REVERSAL_MIN_EDGE",    "0.02"))
UNDERDOG_REVERSAL_MIN_LAG     = float(os.getenv("UNDERDOG_REVERSAL_MIN_LAG",     "0.02"))
# Aegis threshold: leading team must be ahead by this much (gold) before Aegis triggers exit
UNDERDOG_REVERSAL_LEAD_THRESHOLD = int(os.getenv("UNDERDOG_REVERSAL_LEAD_THRESHOLD", "2000"))

BOOK_MOVE_WINDOW_SEC = float(os.getenv("BOOK_MOVE_WINDOW_SEC", "10.0"))
BOOK_MOVE_THRESHOLD = float(os.getenv("BOOK_MOVE_THRESHOLD", "0.04"))
BOOK_MOVE_DEBOUNCE_SEC = float(os.getenv("BOOK_MOVE_DEBOUNCE_SEC", "30.0"))
BOOK_MOVES_CSV_PATH = os.getenv("BOOK_MOVES_CSV_PATH", "logs/book_moves.csv")

CSV_LOG_PATH = os.getenv("CSV_LOG_PATH", "logs/signals.csv")
PAPER_TRADES_CSV_PATH = os.getenv("PAPER_TRADES_CSV_PATH", "logs/paper_trades.csv")
POSITIONS_CSV_PATH = os.getenv("POSITIONS_CSV_PATH", "logs/positions.csv")
PNL_SUMMARY_CSV_PATH = os.getenv("PNL_SUMMARY_CSV_PATH", "logs/pnl_summary.csv")
LATENCY_CSV_PATH = os.getenv("LATENCY_CSV_PATH", "logs/latency.csv")
LIVE_LEAGUE_RAW_CSV_PATH = os.getenv("LIVE_LEAGUE_RAW_CSV_PATH", "logs/liveleague_raw.csv")
RICH_CONTEXT_CSV_PATH = os.getenv("RICH_CONTEXT_CSV_PATH", "logs/rich_context.csv")
LIVE_LEAGUE_RAW_JSONL_PATH = os.getenv("LIVE_LEAGUE_RAW_JSONL_PATH", "logs/liveleague_raw.jsonl")
SOURCE_DELAY_CSV_PATH = os.getenv("SOURCE_DELAY_CSV_PATH", "logs/source_delay.csv")
MARKOUTS_CSV_PATH = os.getenv("MARKOUTS_CSV_PATH", "logs/markouts.csv")
BOOK_REFRESH_RESCUE_CSV_PATH = os.getenv("BOOK_REFRESH_RESCUE_CSV_PATH", "logs/book_refresh_rescue.csv")

if MODE not in {"paper", "live"}:
    raise RuntimeError("MODE must be paper or live-test compatible live.")
if LIVE_TRADING:
    retired_live_events = sorted(TRADE_EVENTS & RETIRED_FIXED_WINDOW_EVENTS)
    if retired_live_events:
        raise RuntimeError(f"TRADE_EVENTS contains retired event names: {','.join(retired_live_events)}")
# Event / reaction-lag logging
DOTA_EVENTS_CSV_PATH = os.getenv("DOTA_EVENTS_CSV_PATH", "logs/dota_events.csv")
BOOK_EVENTS_CSV_PATH = os.getenv("BOOK_EVENTS_CSV_PATH", "logs/book_events.csv")
EVENT_COOLDOWN_GAME_SECONDS = int(os.getenv("EVENT_COOLDOWN_GAME_SECONDS", "15"))
REACTION_WINDOW_SECONDS = int(os.getenv("REACTION_WINDOW_SECONDS", "30"))
BOOK_MOVE_MIN_CENTS = float(os.getenv("BOOK_MOVE_MIN_CENTS", "0.01"))
REALTIME_STATS_ENABLED = os.getenv("REALTIME_STATS_ENABLED", "true").lower() in {"1", "true", "yes"}
REALTIME_STATS_STALE_SEC = int(os.getenv("REALTIME_STATS_STALE_SEC", "30"))


def _config_hash() -> str:
    keys = [
        "LIVE_TRADING", "MAX_TOTAL_LIVE_USD", "MAX_TRADE_USD", "ORDER_TYPE",
        "TRADE_EVENTS", "ALLOW_CONFIRMATION_ONLY_LIVE_TRADES",
        "DISABLE_STRUCTURE_TRADES", "MAX_BOOK_AGE_MS", "MAX_STEAM_AGE_MS",
        "MAX_SOURCE_UPDATE_AGE_SEC", "MIN_LAG", "MIN_EXECUTABLE_EDGE",
        "MAX_SPREAD", "MIN_ASK_SIZE_USD", "LIVE_REQUIRE_CADENCE_SCHEMA",
        "LIVE_ALLOWED_CADENCE_QUALITIES", "LIVE_MIN_EVENT_QUALITY", "PAPER_EXECUTION_DELAY_MS",
        "BOOK_REFRESH_RESCUE_CSV_PATH",
    ]
    payload = {key: os.getenv(key) for key in keys}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:12]


CONFIG_HASH = _config_hash()
