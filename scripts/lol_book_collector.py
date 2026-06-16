"""League of Legends Polymarket order-book data collector + PAPER SCALP.

Standalone script that:
  1. Discovers active LoL markets on Polymarket via page scrape.
  2. Persists/updates lol_markets.yaml (name, match_id, yes/no tokens).
  3. Subscribes to those markets on the CLOB websocket.
  4. Logs every top-of-book change to logs/lol_book_events.csv.
  5. PAPER-trades the buy-both-scalp on qualifying markets, logging pair
     P&L to logs/lol_scalp_paper.csv.

Scalp evaluation runs PAPER-only (no real CLOB orders) — uses the same
filter as the Dota scalp_executor (skew ≤ 0.08, sum ≤ 1.03, both prices
in [0.40, 0.60]).
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import re
from html import unescape
from urllib.parse import urljoin

from poly_gamma import parse_clob_token_ids
from poly_ws import BookStore, ingest_ws_event

LOL_MARKETS_YAML = ROOT / "lol_markets.yaml"
LOL_BOOK_EVENTS_CSV = ROOT / "logs" / "lol_book_events.csv"
LOL_SCALP_PAPER_CSV = ROOT / "logs" / "lol_scalp_paper.csv"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
POLYMARKET_ORIGIN = "https://polymarket.com"
POLYMARKET_LOL_PAGE = "https://polymarket.com/esports/league-of-legends/games"

# ---------- Paper scalp config ----------
SCALP_STAKE_USD = float(os.getenv("LOL_SCALP_STAKE_USD", "50"))
SCALP_MAX_SKEW = float(os.getenv("LOL_SCALP_MAX_SKEW", "0.08"))
SCALP_MAX_SUM = float(os.getenv("LOL_SCALP_MAX_SUM", "1.01"))
SCALP_MIN_PRICE = float(os.getenv("LOL_SCALP_MIN_PRICE", "0.40"))
SCALP_MAX_PRICE = float(os.getenv("LOL_SCALP_MAX_PRICE", "0.60"))
SCALP_SCRATCH_CENTS = float(os.getenv("LOL_SCALP_SCRATCH_CENTS", "0.05"))
SCALP_RIDE_TARGET = float(os.getenv("LOL_SCALP_RIDE_TARGET", "0.90"))
SCALP_FEE_RATE = 0.005

# Improvements (2026-05-27)
SCALP_MIN_BID_SIZE_USD = float(os.getenv("LOL_SCALP_MIN_BID_SIZE_USD", "100"))  # require depth to actually exit
SCALP_STOP_LOSS_CENTS = 0.08   # Tightened from 0.25: audit showed unrecoverable crashes in LoL
SCALP_RIDE_TRAIL_CENTS = 0.05  # exit ride if bid drops 5c from peak
SCALP_RIDE_TRAIL_MIN_PEAK = float(os.getenv("LOL_SCALP_RIDE_TRAIL_MIN_PEAK", "0.60"))  # only arm trail if peak crossed 60c
SCALP_MAX_HOLD_MIN = 45.0           # force-close after 45 min
SCALP_MAX_PAIRS_PER_SERIES = int(os.getenv("LOL_SCALP_MAX_PAIRS_PER_SERIES", "1"))  # 1 pair per BO5 series
SCALP_MAX_OPEN_PAIRS = int(os.getenv("LOL_SCALP_MAX_OPEN_PAIRS", "10"))         # global concurrency cap
SCALP_COOLDOWN_AFTER_LOSS_SEC = float(os.getenv("LOL_SCALP_COOLDOWN_AFTER_LOSS_SEC", "300"))  # 5 min

# In-game tiered filter (LoL has no game-state API, so this only activates when
# the WS supplies a per-market hint via price drift; for now pre-match prices
# dominate. Knobs kept for symmetry with Dota and future use.)
SCALP_EARLY_GAME_SEC = int(os.getenv("LOL_SCALP_EARLY_GAME_SEC", "600"))
SCALP_MAX_GAME_TIME_SEC = int(os.getenv("LOL_SCALP_MAX_GAME_TIME_SEC", "1800"))
SCALP_MID_GAME_MAX_SUM = float(os.getenv("LOL_SCALP_MID_GAME_MAX_SUM", "1.00"))
SCALP_MID_GAME_MAX_SKEW = float(os.getenv("LOL_SCALP_MID_GAME_MAX_SKEW", "0.05"))
SCALP_MID_GAME_MIN_BID_USD = float(os.getenv("LOL_SCALP_MID_GAME_MIN_BID_USD", "200"))

LOL_NEGATIVE_KEYWORDS = [
    "dota", "counter-strike", "cs2", "csgo", "valorant", "rocket league",
    "overwatch", "starcraft", "fortnite", "rainbow six", "bitcoin", "trump",
]


def is_lol_market(market: dict) -> bool:
    """Polymarket's LoL match markets use the `lol-` prefix in their slug.
    Catches: lol-dk-hle1-..., lol-t1-..., lol-lck-..., etc. Excludes
    standings/season markets — we want match-winners that have a clear end.
    """
    slug = str(market.get("slug") or "").lower()
    question = str(market.get("question") or market.get("title") or "").lower()
    if any(neg in slug or neg in question for neg in LOL_NEGATIVE_KEYWORDS):
        return False
    # Match-winner slugs follow lol-<team>-<team>-<YYYY-MM-DD>
    if slug.startswith("lol-") and len(slug.split("-")) >= 5:
        return True
    # Or: question contains "League of Legends" + a vs-style matchup
    if "league of legends" in question and " vs " in question.replace(".", " "):
        return True
    return False


def _ensure_csv_header():
    """Write the header row if the CSV doesn't exist yet."""
    LOL_BOOK_EVENTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    if not LOL_BOOK_EVENTS_CSV.exists() or LOL_BOOK_EVENTS_CSV.stat().st_size == 0:
        with LOL_BOOK_EVENTS_CSV.open("w", newline="") as f:
            csv.writer(f).writerow([
                "timestamp_utc", "asset_id", "best_bid", "best_ask",
                "bid_size", "ask_size", "source_event_type",
            ])


def _log_book(book: dict, source_event_type: str):
    ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    with LOL_BOOK_EVENTS_CSV.open("a", newline="") as f:
        csv.writer(f).writerow([
            ts, book.get("asset_id"),
            book.get("best_bid"), book.get("best_ask"),
            book.get("bid_size"), book.get("ask_size"),
            source_event_type,
        ])


# ---------------- Paper scalp state ----------------

import re as _re


def _series_key(question: str) -> str:
    """Derive a series key from question text so 'Game 1/2/3' all share it.

    E.g. 'LoL: Barczaca vs Forsaken - Game 1 Winner' → 'lol: barczaca vs forsaken'
    """
    q = (question or "").lower()
    q = _re.sub(r"\s*-\s*game\s*\d+\s*winner\s*$", "", q)
    q = _re.sub(r"\s*\(bo\d+\)\s*-\s*.*$", "", q)
    return q.strip()


class LolScalpPair:
    """In-memory state for one buy-both scalp pair (paper)."""
    __slots__ = ("market_id", "question", "series_key", "yes_token", "no_token",
                 "yes_entry_px", "no_entry_px",
                 "yes_scratched_px", "no_scratched_px",
                 "yes_stopped_px", "no_stopped_px",
                 "ride_token", "ride_peak_bid", "ride_armed_trail",
                 "opened_at", "closed", "close_reason",
                 "yes_pnl", "no_pnl")

    def __init__(self, market_id, question, yes_token, no_token, yes_entry, no_entry):
        self.market_id = market_id; self.question = question
        self.series_key = _series_key(question)
        self.yes_token = yes_token; self.no_token = no_token
        self.yes_entry_px = yes_entry; self.no_entry_px = no_entry
        self.yes_scratched_px = None; self.no_scratched_px = None
        self.yes_stopped_px = None; self.no_stopped_px = None
        self.ride_token = None; self.ride_peak_bid = 0.0
        self.ride_armed_trail = False
        self.opened_at = datetime.now(timezone.utc)
        self.closed = False; self.close_reason = ""
        self.yes_pnl = 0.0; self.no_pnl = 0.0


def _bid_dollar_size(book: dict, side: str) -> float:
    """Top-of-book value in $ (bid_size × bid OR ask_size × ask)."""
    if side == "bid":
        b = book.get("bid"); s = book.get("bid_size") or 0
    else:
        b = book.get("ask"); s = book.get("ask_size") or 0
    if b is None: return 0.0
    return float(b) * float(s)


def _qualifies(yes_ask, no_ask, yes_book=None, no_book=None):
    """Entry filter with depth + spread sanity checks."""
    if yes_ask is None or no_ask is None: return False, "missing_ask"
    if not (SCALP_MIN_PRICE <= yes_ask <= SCALP_MAX_PRICE):
        return False, f"yes_px_oob:{yes_ask:.3f}"
    if not (SCALP_MIN_PRICE <= no_ask <= SCALP_MAX_PRICE):
        return False, f"no_px_oob:{no_ask:.3f}"
    skew = abs(yes_ask - no_ask)
    if skew > SCALP_MAX_SKEW: return False, f"skew:{skew:.3f}"
    s_sum = yes_ask + no_ask
    if s_sum > SCALP_MAX_SUM: return False, f"sum:{s_sum:.3f}"
    # Depth: require both BIDS to have ≥ $MIN_BID_SIZE so scratch can fill.
    # We use bid_size_$ because that's what matters for our sell exits.
    for label, book in (("yes", yes_book), ("no", no_book)):
        if book is None: continue
        bid_usd = _bid_dollar_size(book, "bid")
        if bid_usd < SCALP_MIN_BID_SIZE_USD:
            return False, f"{label}_bid_size_${bid_usd:.0f}_below_{SCALP_MIN_BID_SIZE_USD:.0f}"
    # Spread: ensure round-trip cost (buy_ask - sell_bid) is reasonable.
    # If bid is much lower than ask, scratch at +2c may not actually clear.
    for label, ask, book in (("yes", yes_ask, yes_book), ("no", no_ask, no_book)):
        if book is None: continue
        bid = book.get("bid")
        if bid is not None and (ask - bid) > 0.04:
            return False, f"{label}_spread_{ask-bid:.3f}_too_wide"
    return True, ""


def _scratch_target(entry_px):
    return round(entry_px + SCALP_SCRATCH_CENTS, 3)


def _leg_pnl(entry_px, exit_px):
    """Per-leg P&L in $ on SCALP_STAKE_USD notional."""
    shares = SCALP_STAKE_USD / max(entry_px, 0.01)
    gross = (exit_px - entry_px) * shares
    fees = (entry_px + exit_px) * shares * SCALP_FEE_RATE
    return gross - fees


def _ensure_scalp_csv():
    LOL_SCALP_PAPER_CSV.parent.mkdir(parents=True, exist_ok=True)
    if not LOL_SCALP_PAPER_CSV.exists() or LOL_SCALP_PAPER_CSV.stat().st_size == 0:
        with LOL_SCALP_PAPER_CSV.open("w", newline="") as f:
            csv.writer(f).writerow([
                "closed_at_utc", "market_id", "question",
                "yes_entry_px", "no_entry_px",
                "yes_scratched_px", "no_scratched_px",
                "ride_token", "ride_peak_bid",
                "yes_pnl", "no_pnl", "total_pnl_usd",
                "close_reason", "duration_sec",
            ])


def _log_scalp_pair(pair: LolScalpPair):
    _ensure_scalp_csv()
    duration = (datetime.now(timezone.utc) - pair.opened_at).total_seconds()
    ride_side = "YES" if pair.ride_token == pair.yes_token else (
        "NO" if pair.ride_token == pair.no_token else "")
    with LOL_SCALP_PAPER_CSV.open("a", newline="") as f:
        csv.writer(f).writerow([
            datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            pair.market_id, pair.question[:80],
            pair.yes_entry_px, pair.no_entry_px,
            pair.yes_scratched_px, pair.no_scratched_px,
            ride_side, pair.ride_peak_bid,
            round(pair.yes_pnl, 4), round(pair.no_pnl, 4),
            round(pair.yes_pnl + pair.no_pnl, 4),
            pair.close_reason, round(duration, 1),
        ])


# state shared across loops
_scalp_pairs: dict[str, LolScalpPair] = {}
_scalp_evaluated: set[str] = set()      # market_ids we've already checked
_latest_book: dict[str, dict] = {}      # token_id -> {bid, ask, bid_size, ask_size, ts}
_series_open_count: dict[str, int] = {} # series_key -> number of pairs currently open
_cooldown_until_ns: int = 0             # global cooldown after a losing pair
_token_to_market_cache: dict[str, dict] = {}  # refreshed by asset_id_sync_loop


def _open_pair_count() -> int:
    return sum(1 for p in _scalp_pairs.values() if not p.closed)


def _scalp_on_book_update(asset_id: str, book: dict, markets_by_token: dict):
    """Per-tick scalp evaluation + state advance. Paper-only."""
    bid = book.get("best_bid"); ask = book.get("best_ask")
    bsz = book.get("bid_size"); asz = book.get("ask_size")
    if bid is None and ask is None: return
    # Update latest top-of-book for this token (preserving prior sides)
    cur = _latest_book.get(asset_id, {})
    if bid is not None: cur["bid"] = bid
    if ask is not None: cur["ask"] = ask
    if bsz is not None: cur["bid_size"] = bsz
    if asz is not None: cur["ask_size"] = asz
    cur["ts"] = datetime.now(timezone.utc)
    _latest_book[asset_id] = cur

    mkt_info = markets_by_token.get(asset_id)
    if not mkt_info: return
    market_id = mkt_info["market_id"]
    yes_tok = mkt_info["yes_token"]; no_tok = mkt_info["no_token"]
    yes_b = _latest_book.get(yes_tok, {}); no_b = _latest_book.get(no_tok, {})

    now = datetime.now(timezone.utc)
    now_ns = int(now.timestamp() * 1e9)

    # ---------- ENTRY ----------
    if market_id not in _scalp_pairs:
        if market_id in _scalp_evaluated: return
        if now_ns < _cooldown_until_ns: return  # global cooldown
        if _open_pair_count() >= SCALP_MAX_OPEN_PAIRS: return
        yes_ask = yes_b.get("ask"); no_ask = no_b.get("ask")
        # Per-series cap (1 pair per BO5)
        series = _series_key(mkt_info.get("question", ""))
        if series and _series_open_count.get(series, 0) >= SCALP_MAX_PAIRS_PER_SERIES:
            return
        ok, why = _qualifies(yes_ask, no_ask, yes_b, no_b)
        if ok:
            pair = LolScalpPair(market_id, mkt_info["question"], yes_tok, no_tok,
                                 yes_ask, no_ask)
            _scalp_pairs[market_id] = pair
            _scalp_evaluated.add(market_id)
            if pair.series_key:
                _series_open_count[pair.series_key] = _series_open_count.get(pair.series_key, 0) + 1
            print(f"[scalp] OPEN {mkt_info['question'][:60]}  yes={yes_ask:.3f} no={no_ask:.3f}  "
                  f"yes_bid_size=${_bid_dollar_size(yes_b,'bid'):.0f}")
        return

    # ---------- ADVANCE STATE ----------
    pair = _scalp_pairs[market_id]
    if pair.closed: return

    # 1) Scratch each leg when bid >= entry + scratch_cents
    if pair.yes_scratched_px is None and yes_b.get("bid") is not None \
       and yes_b["bid"] >= _scratch_target(pair.yes_entry_px):
        pair.yes_scratched_px = yes_b["bid"]
        pair.yes_pnl = _leg_pnl(pair.yes_entry_px, pair.yes_scratched_px)
        print(f"[scalp] YES scratch {pair.question[:40]} @ {pair.yes_scratched_px:.3f}  pnl=${pair.yes_pnl:+.2f}")

    if pair.no_scratched_px is None and no_b.get("bid") is not None \
       and no_b["bid"] >= _scratch_target(pair.no_entry_px):
        pair.no_scratched_px = no_b["bid"]
        pair.no_pnl = _leg_pnl(pair.no_entry_px, pair.no_scratched_px)
        print(f"[scalp] NO scratch {pair.question[:40]} @ {pair.no_scratched_px:.3f}  pnl=${pair.no_pnl:+.2f}")

    # 2) Stop-loss on un-scratched legs (cut at entry - stop_loss_cents)
    if pair.yes_scratched_px is None and pair.yes_stopped_px is None \
       and yes_b.get("bid") is not None \
       and yes_b["bid"] <= pair.yes_entry_px - SCALP_STOP_LOSS_CENTS:
        pair.yes_stopped_px = yes_b["bid"]
        pair.yes_pnl = _leg_pnl(pair.yes_entry_px, pair.yes_stopped_px)
        print(f"[scalp] YES STOP {pair.question[:40]} @ {pair.yes_stopped_px:.3f}  pnl=${pair.yes_pnl:+.2f}")

    if pair.no_scratched_px is None and pair.no_stopped_px is None \
       and no_b.get("bid") is not None \
       and no_b["bid"] <= pair.no_entry_px - SCALP_STOP_LOSS_CENTS:
        pair.no_stopped_px = no_b["bid"]
        pair.no_pnl = _leg_pnl(pair.no_entry_px, pair.no_stopped_px)
        print(f"[scalp] NO STOP {pair.question[:40]} @ {pair.no_stopped_px:.3f}  pnl=${pair.no_pnl:+.2f}")

    # 3) Identify ride side (the leg that scratched first; the other rides)
    if pair.ride_token is None:
        yes_done = pair.yes_scratched_px is not None or pair.yes_stopped_px is not None
        no_done = pair.no_scratched_px is not None or pair.no_stopped_px is not None
        if yes_done and not no_done:
            pair.ride_token = pair.no_token
            # Emergency Exit: if the "ride" leg is already underwater when the other scratches, kill it.
            # In LoL, a +5c scratch is often accompanied by a -20c crash on the other side.
            if no_b.get("bid") is not None and no_b["bid"] < pair.no_entry_px:
                pair.no_stopped_px = no_b["bid"]
                pair.no_pnl = _leg_pnl(pair.no_entry_px, pair.no_stopped_px)
                _close_pair(pair, "emergency_underwater_ride_cut")
                return
        elif no_done and not yes_done:
            pair.ride_token = pair.yes_token
            if yes_b.get("bid") is not None and yes_b["bid"] < pair.yes_entry_px:
                pair.yes_stopped_px = yes_b["bid"]
                pair.yes_pnl = _leg_pnl(pair.yes_entry_px, pair.yes_stopped_px)
                _close_pair(pair, "emergency_underwater_ride_cut")
                return

    # 4) Ride: track peak, trail, take-profit
    if pair.ride_token is not None:
        ride_b = no_b if pair.ride_token == pair.no_token else yes_b
        ride_bid = ride_b.get("bid")
        ride_entry = pair.no_entry_px if pair.ride_token == pair.no_token else pair.yes_entry_px
        if ride_bid is not None and ride_bid > pair.ride_peak_bid:
            pair.ride_peak_bid = ride_bid
        # Arm trailing stop once we cross the trail-min-peak
        if pair.ride_peak_bid >= SCALP_RIDE_TRAIL_MIN_PEAK:
            pair.ride_armed_trail = True

        if ride_bid is not None:
            close_via = None
            if ride_bid >= SCALP_RIDE_TARGET:
                close_via = f"ride_tp_at_{ride_bid:.3f}"
            elif pair.ride_armed_trail and ride_bid <= pair.ride_peak_bid - SCALP_RIDE_TRAIL_CENTS:
                close_via = f"ride_trail_peak{pair.ride_peak_bid:.3f}_exit{ride_bid:.3f}"
            if close_via:
                ride_pnl = _leg_pnl(ride_entry, ride_bid)
                if pair.ride_token == pair.no_token: pair.no_pnl = ride_pnl
                else: pair.yes_pnl = ride_pnl
                _close_pair(pair, close_via)
                return

    # 5) Auto-close if both legs are done (scratched or stopped)
    yes_done = pair.yes_scratched_px is not None or pair.yes_stopped_px is not None
    no_done = pair.no_scratched_px is not None or pair.no_stopped_px is not None
    if yes_done and no_done and not pair.closed:
        _close_pair(pair, "both_legs_closed")
        return

    # 6) Time-based exit
    age_min = (now - pair.opened_at).total_seconds() / 60.0
    if age_min >= SCALP_MAX_HOLD_MIN and not pair.closed:
        # Force-close any open legs at current bid
        if not yes_done and yes_b.get("bid") is not None:
            pair.yes_stopped_px = yes_b["bid"]
            pair.yes_pnl = _leg_pnl(pair.yes_entry_px, pair.yes_stopped_px)
        if not no_done and no_b.get("bid") is not None:
            pair.no_stopped_px = no_b["bid"]
            pair.no_pnl = _leg_pnl(pair.no_entry_px, pair.no_stopped_px)
        _close_pair(pair, f"max_hold_{age_min:.0f}min")


def _close_pair(pair: LolScalpPair, reason: str):
    """Mark pair closed, log, update series count + global cooldown on losses."""
    global _cooldown_until_ns
    pair.closed = True
    pair.close_reason = reason
    _log_scalp_pair(pair)
    if pair.series_key and _series_open_count.get(pair.series_key, 0) > 0:
        _series_open_count[pair.series_key] -= 1
    total = pair.yes_pnl + pair.no_pnl
    print(f"[scalp] CLOSE {reason} {pair.question[:40]}  total=${total:+.2f}  "
          f"(yes=${pair.yes_pnl:+.2f} no=${pair.no_pnl:+.2f}  peak={pair.ride_peak_bid:.3f})")
    # Cooldown on losses
    if total < 0:
        _cooldown_until_ns = int(datetime.now(timezone.utc).timestamp() * 1e9) \
                              + int(SCALP_COOLDOWN_AFTER_LOSS_SEC * 1e9)
        print(f"[scalp] cooldown {SCALP_COOLDOWN_AFTER_LOSS_SEC:.0f}s after loss")


def load_markets() -> dict[str, dict]:
    """Load lol_markets.yaml → {market_id: {name, yes_token, no_token, ...}}."""
    if not LOL_MARKETS_YAML.exists():
        return {}
    try:
        data = yaml.safe_load(LOL_MARKETS_YAML.read_text()) or {}
    except Exception:
        return {}
    out = {}
    for m in data.get("markets", []):
        mid = str(m.get("market_id", "") or m.get("question_id", "") or m.get("slug", ""))
        if not mid:
            continue
        out[mid] = m
    return out


def save_markets(markets: dict[str, dict]):
    LOL_MARKETS_YAML.write_text(yaml.safe_dump(
        {"markets": list(markets.values())}, sort_keys=False, default_flow_style=False,
    ))


def _extract_next_data(html: str) -> dict:
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, flags=re.S)
    if not match: return {}
    try: return json.loads(unescape(match.group(1)))
    except json.JSONDecodeError: return {}


def _walk_markets(obj) -> list[dict]:
    out: list[dict] = []
    if isinstance(obj, dict):
        if "clobTokenIds" in obj and ("question" in obj or "title" in obj):
            out.append(obj)
        for v in obj.values(): out.extend(_walk_markets(v))
    elif isinstance(obj, list):
        for v in obj: out.extend(_walk_markets(v))
    return out


def _extract_lol_event_urls(html: str) -> list[str]:
    """Pick lol-* event hrefs from the LoL games page."""
    hrefs = re.findall(r'href="(/event/lol-[^"]+)"', html)
    out = []
    seen = set()
    for href in hrefs:
        href = unescape(href)
        url = urljoin(POLYMARKET_ORIGIN, href)
        if url in seen: continue
        seen.add(url); out.append(url)
    return out


_MAP_WINNER_RE = re.compile(r"\bGame\s*\d+\s+Winner\b", re.I)
_SERIES_WINNER_RE = re.compile(r"\((BO[35])\)", re.I)
_NOISE_MARKETS = ("handicap", "odd/even", "first blood", "kills", "duration",
                  "first to", "total", "dragon", "tower", "baron")


async def _fetch_event_markets(session: aiohttp.ClientSession, url: str) -> list[dict]:
    headers = {"user-agent": "Mozilla/5.0", "Accept-Encoding": "gzip, deflate"}
    try:
        async with session.get(url, timeout=15, headers=headers) as r:
            r.raise_for_status()
            html = await r.text()
    except Exception as exc:
        print(f"[discover] {url} fetch error: {exc}")
        return []
    data = _extract_next_data(html)
    markets: list[dict] = []
    for m in _walk_markets(data):
        outcomes = m.get("outcomes")
        if isinstance(outcomes, str):
            try: outcomes = json.loads(outcomes)
            except: outcomes = []
        if not outcomes or len(outcomes) != 2: continue
        labels = {str(o).casefold() for o in outcomes}
        if labels <= {"yes", "no", "over", "under"}: continue
        q = str(m.get("question") or m.get("title") or "").lower()
        if any(n in q for n in _NOISE_MARKETS): continue  # skip handicap, odd/even, etc.
        # Keep only per-map winner OR BO3/BO5 series winners
        question = str(m.get("question") or m.get("title") or "")
        is_map = bool(_MAP_WINNER_RE.search(question))
        is_series = bool(_SERIES_WINNER_RE.search(question))
        if not (is_map or is_series): continue
        m["source_url"] = url
        m["market_type"] = "MAP_WINNER" if is_map else "MATCH_WINNER"
        markets.append(m)
    return markets


async def discover_loop(*, interval_sec: int = 300):
    """Every interval, scrape the LoL games page for match-winner markets."""
    while True:
        try:
            headers = {"user-agent": "Mozilla/5.0", "Accept-Encoding": "gzip, deflate"}
            async with aiohttp.ClientSession() as session:
                async with session.get(POLYMARKET_LOL_PAGE, timeout=15, headers=headers) as r:
                    r.raise_for_status()
                    listing_html = await r.text()
                event_urls = _extract_lol_event_urls(listing_html)
                if not event_urls:
                    print(f"[discover] no lol-* events found on listing page; check URL")
                    await asyncio.sleep(interval_sec); continue
                results = await asyncio.gather(*(_fetch_event_markets(session, u) for u in event_urls))
            all_markets = [m for sub in results for m in sub]

            existing = load_markets()
            added = 0
            for m in all_markets:
                yes_tok, no_tok = parse_clob_token_ids(m)
                if not yes_tok or not no_tok: continue
                mid = str(m.get("conditionId") or m.get("id") or m.get("slug") or yes_tok[:16])
                if mid in existing: continue
                # Extract team labels from outcomes for richer mapping
                outcomes = m.get("outcomes")
                if isinstance(outcomes, str):
                    try: outcomes = json.loads(outcomes)
                    except: outcomes = []
                yes_team = (outcomes[0] if outcomes and len(outcomes) >= 1 else "")
                no_team = (outcomes[1] if outcomes and len(outcomes) >= 2 else "")
                entry = {
                    "market_id": mid, "condition_id": m.get("conditionId"),
                    "question": m.get("question") or m.get("title"),
                    "slug": m.get("slug"),
                    "source_url": m.get("source_url"),
                    "yes_team": yes_team, "no_team": no_team,
                    "yes_token_id": yes_tok, "no_token_id": no_tok,
                    "tick_size": m.get("tickSize") or "0.01",
                    "neg_risk": bool(m.get("negRisk", False)),
                    "discovered_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "end_date": m.get("endDate") or m.get("end_date_iso"),
                }
                existing[mid] = entry
                added += 1
                print(f"  + {entry['question']}")
            if added:
                save_markets(existing)
                print(f"[discover] added {added} new LoL markets, total {len(existing)}")
            else:
                print(f"[discover] no new markets; {len(existing)} total, "
                       f"{len(event_urls)} events on listing, {len(all_markets)} match-winners")
        except Exception as exc:
            print(f"[discover] error: {exc}")
        await asyncio.sleep(interval_sec)


SILENCE_RECONNECT_SEC = int(os.getenv("LOL_WS_SILENCE_RECONNECT_SEC", "600"))


async def ws_loop(asset_ids: list[str]):
    """Subscribe to all LoL asset IDs, log every top-of-book change to CSV.

    Silence policy: WS ping/pong keeps the TCP connection alive (20s interval),
    so prolonged silence between book events is normal when LoL games are idle.
    Only force a reconnect if NO messages have arrived for SILENCE_RECONNECT_SEC
    (default 10 min) — that indicates a real connection problem, not just market
    quiet. Reconnects on asset-set changes happen immediately regardless.
    """
    import websockets
    store = BookStore()
    subscribed: set[str] = set()
    while True:
        clean_ids = [str(a) for a in asset_ids if a]
        if not clean_ids:
            await asyncio.sleep(15)
            continue
        try:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
                await ws.send(json.dumps({"assets_ids": clean_ids, "type": "market"}))
                subscribed = set(clean_ids)
                print(f"[ws] subscribed to {len(clean_ids)} LoL assets "
                       f"(silence reconnect after {SILENCE_RECONNECT_SEC}s)")
                last_msg_at = time.time()
                while True:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
                        last_msg_at = time.time()
                    except asyncio.TimeoutError:
                        if time.time() - last_msg_at > SILENCE_RECONNECT_SEC:
                            print(f"[ws] {SILENCE_RECONNECT_SEC}s silence — reconnecting")
                            break
                        # Check if asset set changed (drives reconnect)
                        cur = {str(a) for a in asset_ids if a}
                        if cur != subscribed:
                            print(f"[ws] asset set changed ({len(cur)} now) — reconnecting")
                            break
                        continue
                    try:
                        data = json.loads(msg)
                    except Exception:
                        continue
                    events = data if isinstance(data, list) else [data]
                    # Use cached token→market index (refreshed by asset_id_sync_loop)
                    tok_to_mkt = _token_to_market_cache
                    for event in events:
                        for book, src_type in ingest_ws_event(event, store):
                            _log_book(book, src_type)
                            try:
                                _scalp_on_book_update(book["asset_id"], book, tok_to_mkt)
                            except Exception as _err:
                                print(f"[scalp] error on update: {_err}")
        except Exception as exc:
            print(f"[ws] error: {exc} — reconnecting in 5s")
            await asyncio.sleep(5)


async def asset_id_sync_loop(asset_ids: list[str]):
    """Keep the asset_ids list AND the token→market cache in sync every 30s."""
    while True:
        markets = load_markets()
        wanted = []
        new_cache = {}
        for m in markets.values():
            yes = str(m.get("yes_token_id") or "")
            no = str(m.get("no_token_id") or "")
            info = {"market_id": m["market_id"], "question": m.get("question", ""),
                    "yes_token": yes, "no_token": no}
            if yes:
                wanted.append(yes); new_cache[yes] = info
            if no:
                wanted.append(no); new_cache[no] = info
        # Atomic-ish swap into the global cache (Python dict assignment)
        _token_to_market_cache.clear()
        _token_to_market_cache.update(new_cache)
        if set(wanted) != set(asset_ids):
            asset_ids[:] = wanted
            print(f"[sync] asset list updated → {len(asset_ids)} tokens, cache={len(_token_to_market_cache)}")
        await asyncio.sleep(30)


async def main():
    _ensure_csv_header()
    print(f"LoL collector starting. CSV → {LOL_BOOK_EVENTS_CSV}")
    print(f"Markets file → {LOL_MARKETS_YAML}")
    asset_ids: list[str] = []
    # Initial population (also seed the token→market cache)
    for m in load_markets().values():
        yt = str(m.get("yes_token_id") or ""); nt = str(m.get("no_token_id") or "")
        info = {"market_id": m["market_id"], "question": m.get("question", ""),
                "yes_token": yt, "no_token": nt}
        if yt: asset_ids.append(yt); _token_to_market_cache[yt] = info
        if nt: asset_ids.append(nt); _token_to_market_cache[nt] = info
    print(f"Loaded {len(asset_ids)} tokens from {LOL_MARKETS_YAML.name}, cache={len(_token_to_market_cache)}")

    await asyncio.gather(
        discover_loop(interval_sec=300),       # check Gamma every 5 min
        asset_id_sync_loop(asset_ids),         # rebuild asset list every 30s
        ws_loop(asset_ids),                    # subscribe + log books
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nstopped.")
