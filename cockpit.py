#!/usr/bin/env python3
"""Manual trading cockpit — watch one live Dota match's GetTopLive state and the
Polymarket book side-by-side, and fire a FAK order with one keypress.

Usage:
    python3 cockpit.py                 # list live mapped matches, pick one
    python3 cockpit.py nemesis         # auto-pick first live match matching text
    python3 cockpit.py 8836220468      # auto-pick by Steam match_id

Hotkeys (in cockpit):
    b      arm BUY of yes_team   (then y/ENTER = fire, any other key = cancel)
    n      arm BUY of no_team
    s      arm SELL (dump ALL) of yes_team position (protective floor = mid - slip)
    x      arm SELL (dump ALL) of no_team position
    l      arm LIMIT-SELL of yes_team position @ limit_px (resting GTC, take-profit)
    ;      arm LIMIT-SELL of no_team position @ limit_px
    , / .  lower / raise the limit-sell price by 1c
    c      arm CANCEL of all my resting orders on this market
    + / -  adjust order size by $5
    [ / ]  adjust price-cap buffer by 1c (slippage allowance over ask)
    d      toggle DECIDER override (treat a BO3 moneyline as the Game-3 map winner,
           so the model fair is valid — use when the binder's series score is stale)
    r      force refresh
    q      quit

Respects .env MODE: if MODE != live OR ENABLE_REAL_LIVE_TRADING != true, orders
are SIMULATED (printed, not sent). Otherwise REAL CLOB orders are placed.
"""
from __future__ import annotations

import asyncio
try:
    import curses
except ImportError:
    curses = None  # UI will fail, but API wrapper functions can still be imported
import os
import sys
import threading
import time
from datetime import datetime, timezone

import aiohttp
import yaml
from dotenv import load_dotenv

sys.path.append(os.getcwd())
from steam_client import fetch_all_live_games
from book_refresh import CLOB_BOOK_URL
try:
    import winprob as _winprob
except Exception:
    _winprob = None
try:
    from market_scope import is_game3_match_proxy as _is_g3
except Exception:
    _is_g3 = lambda m: False


def market_tag(m: dict):
    """(sort_key, short, long) describing a market. MAP and game-3-DECIDER series
    sort first (they price as single-game). A non-decider MATCH_WINNER (series) is
    flagged separately because the single-game model fair is NOT valid for it."""
    mt = str(m.get("market_type") or "").upper()
    sy, sn = m.get("series_score_yes"), m.get("series_score_no")
    score = f"{sy}-{sn}" if sy is not None and sn is not None else "?"
    gn = m.get("current_game_number") or m.get("game_number") or "?"
    if mt == "MAP_WINNER":
        return (0, "MAP", "MAP/GAME book (single-game)")
    if mt == "MATCH_WINNER":
        if _is_g3(m):
            return (0, "ML=MAP3", f"SERIES = MAP3 DECIDER (G3 @ {score}) — ML==map winner")
        return (2, "SERIES", f"SERIES/MONEYLINE (BO3, G{gn} @ {score}) — model fair NOT valid unless decider")
    return (1, mt or "?", mt)

load_dotenv()

MODE = os.getenv("MODE", "paper").lower()
REAL = MODE == "live" and os.getenv("ENABLE_REAL_LIVE_TRADING", "false").lower() == "true"

GAME_POLL_SEC = 1.0
BOOK_POLL_SEC = 1.0
HOLD_POLL_SEC = 5.0
GAME_STALE_SEC = 30.0   # keep last game snapshot this long when it flickers out of GetTopLive


def record_order(action, side, token, amount, price, resp):
    import json
    try:
        with open("logs/cockpit_orders.jsonl", "a") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "action": action,
                                "side": side, "token": token, "amount": amount, "price": price,
                                "resp": resp}) + "\n")
    except Exception:
        pass

STATE: dict = {
    "game": None, "game_ts": 0.0,
    "book": None, "book_no": None, "book_ts": 0.0,
    "hold_yes": None, "hold_no": None, "hold_ts": 0.0,
    "cost_yes": 0.0, "cost_no": 0.0,   # session $ spent buying each side (cost basis)
    "cash": None,                      # USDC collateral balance
    "missed_polls": 0,                 # consecutive GetTopLive polls without this game
    "open_orders": None,               # count of my resting orders on this market
    "log": [], "order_request": None, "stop": False, "worker_dead": None,
}
LOCK = threading.Lock()


def logmsg(s: str) -> None:
    with LOCK:
        STATE["log"].append(f"{datetime.now().strftime('%H:%M:%S')} {s}")
        STATE["log"] = STATE["log"][-8:]


# ---------------------------------------------------------------- market lookup
def load_markets() -> list[dict]:
    with open("markets.yaml") as f:
        return yaml.safe_load(f).get("markets", [])


def market_for_match(markets: list[dict], match_id: str) -> dict | None:
    for m in markets:
        if str(m.get("dota_match_id") or "") == str(match_id):
            return m
    return None


async def list_live_mapped(markets: list[dict]) -> list[tuple[dict, dict]]:
    """Return (game, market) pairs for live games that have a bound market.

    IMPORTANT: a single live game's match_id is bound to MULTIPLE markets — the
    series moneyline (MATCH_WINNER, e.g. "... (BO3)") AND the map winner
    (MAP_WINNER, e.g. "... Game 2 Winner"). We emit ONE pair PER market and sort
    MAP_WINNER first, so the picker shows every book distinctly (the old
    'first match' returned the moneyline and you'd trade the wrong book)."""
    async with aiohttp.ClientSession() as s:
        games = await fetch_all_live_games(s, include_league=True)
    out = []
    for g in games:
        if g.get("game_over"):
            continue
        matched = [m for m in markets
                   if str(m.get("dota_match_id") or "") == str(g.get("match_id"))]
        # single-game-equivalent books first (MAP, and game-3 DECIDER series),
        # then plain series moneylines (model fair not valid for those).
        matched.sort(key=lambda m: market_tag(m)[0])
        for m in matched:
            out.append((g, m))
    return out


# ---------------------------------------------------------------- CLOB client
def make_client():
    from py_clob_client_v2 import ApiCreds, ClobClient
    creds = ApiCreds(
        api_key=os.getenv("POLY_CLOB_API_KEY"),
        api_secret=os.getenv("POLY_CLOB_SECRET"),
        api_passphrase=os.getenv("POLY_CLOB_PASS_PHRASE"),
    )
    return ClobClient(
        host=os.getenv("POLY_CLOB_HOST", "https://clob.polymarket.com"),
        chain_id=int(os.getenv("POLY_CHAIN_ID", "137")),
        key=os.getenv("POLY_PRIVATE_KEY"),
        creds=creds,
        signature_type=int(os.getenv("POLY_SIGNATURE_TYPE", "2")),
        funder=os.getenv("POLY_FUNDER_ADDRESS"),
    )


def place_order(client, token_id, amount, price_cap, tick, neg_risk):
    from py_clob_client_v2 import MarketOrderArgs, OrderType, PartialCreateOrderOptions, Side
    args = MarketOrderArgs(token_id=token_id, amount=float(amount), side=Side.BUY, price=float(price_cap))
    opts = PartialCreateOrderOptions(tick_size=str(tick), neg_risk=bool(neg_risk))
    return client.create_and_post_market_order(order_args=args, options=opts, order_type=OrderType.FAK)


def get_shares(client, token_id) -> float:
    """CLOB conditional-token balance in shares (6-decimal)."""
    from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams
    resp = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=str(token_id)))
    raw = resp.get("balance") if isinstance(resp, dict) else getattr(resp, "balance", None)
    try:
        return float(raw) / 1_000_000.0 if raw is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def get_cash(client) -> float | None:
    """USDC collateral balance in dollars."""
    from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams
    resp = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    raw = resp.get("balance") if isinstance(resp, dict) else getattr(resp, "balance", None)
    try:
        return float(raw) / 1_000_000.0 if raw is not None else None
    except (TypeError, ValueError):
        return None


def _order_fields(o):
    od = o if isinstance(o, dict) else getattr(o, "__dict__", {})
    asset = str(od.get("asset_id") or od.get("token_id") or od.get("market") or "")
    oid = od.get("id") or od.get("order_id") or od.get("orderID") or od.get("orderId")
    return asset, oid


def my_open_order_ids(client, yes_token, no_token):
    """Open order IDs that belong to THIS market's two tokens (so we never touch
    the bot's orders on other markets)."""
    toks = {str(yes_token), str(no_token)}
    oo = client.get_open_orders() or []
    return [oid for (asset, oid) in (_order_fields(o) for o in oo) if asset in toks and oid]


def cancel_my_orders(client, yes_token, no_token):
    try:
        ids = my_open_order_ids(client, yes_token, no_token)
    except Exception as e:
        return {"error": str(e)[:60]}
    if not ids:
        return {"cancelled": 0}
    try:
        client.cancel_orders(ids)
        return {"cancelled": len(ids)}
    except Exception as e:
        return {"error": str(e)[:60], "tried": len(ids)}


def place_sell(client, token_id, shares, price_floor, tick, neg_risk):
    """GTC limit SELL at price_floor (use the bid) — crosses and fills immediately
    if a bid exists; dumps the position fast. No USDC needed to sell."""
    from py_clob_client_v2.clob_types import OrderArgsV2, OrderType, PartialCreateOrderOptions
    args = OrderArgsV2(token_id=str(token_id), price=float(price_floor), size=float(shares), side="SELL")
    opts = PartialCreateOrderOptions(tick_size=str(tick), neg_risk=bool(neg_risk) or None)
    return client.create_and_post_order(args, opts, OrderType.GTC)


# ---------------------------------------------------------------- depth & planning
async def fetch_depth(session, token_id, timeout_ms=1800):
    """Full order-book depth: returns {asks:[(price,size)..asc], bids:[(price,size)..desc], ...}."""
    try:
        async with session.get(CLOB_BOOK_URL, params={"token_id": str(token_id)},
                               headers={"Accept-Encoding": "gzip, deflate"},  # avoid 'br' which aiohttp can't decode
                               timeout=aiohttp.ClientTimeout(total=timeout_ms / 1000.0)) as r:
            if r.status != 200:
                return None
            data = await r.json()
    except Exception:
        return None

    def _levels(rows):
        out = []
        for lv in rows or []:
            try:
                p, s = float(lv["price"]), float(lv.get("size", 0))
            except (KeyError, TypeError, ValueError):
                continue
            if s > 0:
                out.append((p, s))
        return out

    asks = sorted(_levels(data.get("asks")), key=lambda x: x[0])
    bids = sorted(_levels(data.get("bids")), key=lambda x: -x[0])
    ba = asks[0][0] if asks else None
    bb = bids[0][0] if bids else None
    return {
        "asks": asks, "bids": bids,
        "best_ask": ba, "best_bid": bb,
        "ask_size": asks[0][1] if asks else None,
        "bid_size": bids[0][1] if bids else None,
        "spread": (ba - bb) if (ba is not None and bb is not None) else None,
        "received_at_ns": time.time_ns(),
    }


def plan_buy(asks, usd):
    """Walk asks (ascending) spending `usd`. Returns realistic fill."""
    rem, sh, spent, worst = float(usd), 0.0, 0.0, None
    for price, size in asks:
        if rem <= 1e-9:
            break
        take_cost = min(rem, price * size)
        sh += take_cost / price
        spent += take_cost
        rem -= take_cost
        worst = price
    return {"shares": sh, "spent": spent, "vwap": (spent / sh if sh > 0 else None),
            "worst": worst, "full": rem <= 1e-6}


def plan_sell(bids, shares):
    """Walk bids (descending) dumping `shares`. Returns realistic proceeds."""
    rem, sold, proceeds, worst = float(shares), 0.0, 0.0, None
    for price, size in bids:
        if rem <= 1e-6:
            break
        take = min(rem, size)
        sold += take
        proceeds += take * price
        rem -= take
        worst = price
    return {"sold": sold, "proceeds": proceeds, "vwap": (proceeds / sold if sold > 0 else None),
            "worst": worst, "full": rem <= 1e-6}


def sane_sell_floor(depth, slip):
    """Protective sell floor = mid - slip. Refuses to dump below it, so a
    bid-vacuum (bids yanked far below fair) can't fill your order at a steal."""
    ba, bb = (depth or {}).get("best_ask"), (depth or {}).get("best_bid")
    mid = (ba + bb) / 2 if (ba is not None and bb is not None) else (ba or bb)
    if mid is None:
        return 0.01
    return round(max(0.01, mid - slip), 2)


def plan_dump(bids, shares, floor):
    """Sell `shares` but only into bids >= floor. Fills the good bids, rests the
    rest at floor instead of crushing into a vacuum."""
    elig = [(p, s) for (p, s) in (bids or []) if p >= floor]
    pl = plan_sell(elig, shares)
    pl["resting"] = max(0.0, float(shares) - pl["sold"])
    pl["floor"] = floor
    return pl


# ---------------------------------------------------------------- worker thread
def worker(match_id: str, market: dict, yes_token: str, no_token: str, tick, neg_risk):
    async def run():
        client = make_client() if REAL else None
        async with aiohttp.ClientSession() as s:
            last_g = last_b = last_h = 0.0
            while not STATE["stop"]:
                now = time.time()
                if now - last_g >= GAME_POLL_SEC:
                    last_g = now
                    try:
                        games = await fetch_all_live_games(s, include_league=True)
                        g = next((x for x in games if str(x.get("match_id")) == str(match_id)), None)
                        with LOCK:
                            if g is not None:
                                STATE["game"], STATE["game_ts"] = g, now
                                STATE["missed_polls"] = 0
                            else:
                                # Game flickered out of the GetTopLive feed (common in
                                # draft / early game). Keep the last snapshot and let its
                                # age grow rather than blanking the cockpit. Only treat it
                                # as truly gone after a grace window.
                                if (now - STATE["game_ts"]) > GAME_STALE_SEC:
                                    STATE["game"] = None
                                STATE["missed_polls"] = STATE.get("missed_polls", 0) + 1
                    except Exception as e:
                        logmsg(f"game poll err: {str(e)[:40]}")
                if now - last_b >= BOOK_POLL_SEC:
                    last_b = now
                    try:
                        bky = await fetch_depth(s, yes_token, timeout_ms=1800)
                        bkn = await fetch_depth(s, no_token, timeout_ms=1800)
                        with LOCK:
                            STATE["book"], STATE["book_no"], STATE["book_ts"] = bky, bkn, now
                    except Exception as e:
                        logmsg(f"book poll err: {str(e)[:40]}")
                if REAL and now - last_h >= HOLD_POLL_SEC:
                    last_h = now
                    try:
                        hy = await asyncio.to_thread(get_shares, client, yes_token)
                        hn = await asyncio.to_thread(get_shares, client, no_token)
                        try:
                            cash = await asyncio.to_thread(get_cash, client)
                        except Exception:
                            cash = None
                        try:
                            noo = len(await asyncio.to_thread(my_open_order_ids, client, yes_token, no_token))
                        except Exception:
                            noo = None
                        with LOCK:
                            STATE["hold_yes"], STATE["hold_no"], STATE["hold_ts"] = hy, hn, now
                            STATE["open_orders"] = noo
                            if cash is not None:
                                STATE["cash"] = cash
                    except Exception as e:
                        logmsg(f"holdings err: {str(e)[:40]}")
                with LOCK:
                    req = STATE["order_request"]
                    STATE["order_request"] = None
                if req and req["action"] == "cancel":
                    if not REAL:
                        logmsg(f"SIM cancel open orders (MODE={MODE})")
                    else:
                        res = await asyncio.to_thread(cancel_my_orders, client, yes_token, no_token)
                        logmsg(f"CANCEL open orders -> {res}")
                elif req and req["action"] == "limit_sell":
                    side = req["token_side"]; tok = yes_token if side == "yes" else no_token
                    price = float(req.get("price") or 0.95)
                    if not REAL:
                        logmsg(f"SIM LIMIT SELL {side.upper()} @ {price} (MODE={MODE})")
                    else:
                        try:
                            shares = await asyncio.to_thread(get_shares, client, tok)
                            shares = int(shares * 100) / 100.0
                            if shares < 0.01:
                                logmsg(f"LIMIT SELL {side.upper()}: no position")
                            else:
                                resp = await asyncio.to_thread(place_sell, client, tok, shares, price, tick, neg_risk)
                                st = resp.get("status") or resp.get("errorMsg") or "?"
                                logmsg(f"LIMIT SELL {side.upper()} {shares}sh @ {price} -> {st} (rests until filled)")
                                record_order("limit_sell", side, tok, shares, price, resp)
                        except Exception as e:
                            logmsg(f"LIMIT SELL ERROR: {str(e)[:60]}")
                elif req:
                    side = req["token_side"]
                    tok = yes_token if side == "yes" else no_token
                    buf = req.get("buf", 0.01)
                    depth = await fetch_depth(s, tok, timeout_ms=1800)  # depth for the side being traded
                    _vw = lambda v: f"{v:.3f}" if v is not None else "?"
                    if req["action"] == "buy":
                        amt = req["amount"]
                        if not depth or not depth["asks"]:
                            logmsg(f"BUY {side.upper()}: no ask depth")
                        else:
                            pl = plan_buy(depth["asks"], amt)
                            cap = min(round((pl["worst"] or depth["best_ask"]) + buf, 2), 0.99)
                            tag = "" if pl["full"] else f" PARTIAL(only ${pl['spent']:.0f} of ${amt:.0f} in book)"
                            line = f"BUY {side.upper()} ${amt} ~{pl['shares']:.1f}sh vwap {_vw(pl['vwap'])} cap {cap}{tag}"
                            _ckey = "cost_yes" if side == "yes" else "cost_no"
                            if not REAL:
                                logmsg("SIM " + line)
                                with LOCK:
                                    STATE[_ckey] += amt
                            else:
                                import manual_orders
                                manual_orders.enqueue({
                                    "action": "buy",
                                    "match_id": str(match_id),
                                    "token_id": str(tok),
                                    "side": "yes" if side == "yes" else "no",
                                    "size_usd": float(amt),
                                    "price_cap": float(cap)
                                })
                                logmsg(f"{line} -> ENQUEUED TO BOT")
                    else:  # sell — depth-aware dump of the whole position
                        shares = (await asyncio.to_thread(get_shares, client, tok)) if REAL else 0.0
                        shares = int(shares * 100) / 100.0  # floor 2dp, never oversell
                        if REAL and shares < 0.01:
                            logmsg(f"SELL {side.upper()}: no position (0 shares)")
                        elif not depth or not depth["bids"]:
                            logmsg(f"SELL {side.upper()}: no bid depth to sell into")
                        else:
                            floor = sane_sell_floor(depth, buf)   # mid - slip; refuses vacuum dumps
                            pl = plan_dump(depth["bids"], shares, floor)
                            rest_tag = "" if pl["resting"] < 0.01 else f" ({pl['resting']:.1f}sh RESTS @ {floor})"
                            line = f"SELL {side.upper()} {shares}sh floor {floor}: ~{pl['sold']:.1f} fill @ vwap {_vw(pl['vwap'])}{rest_tag}"
                            _ckey = "cost_yes" if side == "yes" else "cost_no"
                            full_fill = pl["resting"] < 0.01
                            if not REAL:
                                logmsg(f"SIM {line} (MODE={MODE})")
                                if full_fill:
                                    with LOCK:
                                        STATE[_ckey] = 0.0
                            else:
                                try:
                                    resp = await asyncio.to_thread(place_sell, client, tok, shares, floor, tick, neg_risk)
                                    st = resp.get("status") or resp.get("errorMsg") or "?"
                                    logmsg(f"{line} -> {st}")
                                    record_order("sell", side, tok, shares, floor, resp)
                                    if full_fill:
                                        with LOCK:
                                            STATE[_ckey] = 0.0   # position flat → reset cost basis
                                except Exception as e:
                                    logmsg(f"SELL ERROR: {str(e)[:60]}")
                await asyncio.sleep(0.2)
    try:
        asyncio.run(run())
    except Exception as e:
        with LOCK:
            STATE["worker_dead"] = str(e)[:80]
        logmsg(f"WORKER FATAL: {str(e)[:80]}")


# ---------------------------------------------------------------- curses UI
def fmt_age(ts):
    if not ts:
        return "--"
    a = time.time() - ts
    return f"{a:.0f}s"


def _vwfmt(v):
    return f"{v:.3f}" if v is not None else "?"


def safe_addstr(win, y, x, s, attr=0):
    """Bounds-checked addstr — clamps to the window so a long line or small
    terminal can't raise curses.error and crash the render loop."""
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x < 0 or x >= w:
        return
    try:
        win.addstr(y, x, str(s)[:max(0, w - x - 1)], attr)
    except curses.error:
        pass


def render_ladder(stdscr, y0, x, label, depth, n=3):
    """Draw one outcome's depth ladder at column x. Returns the next free row."""
    asks = depth.get("asks") if depth else None
    bids = depth.get("bids") if depth else None
    ask = depth.get("best_ask") if depth else None
    bid = depth.get("best_bid") if depth else None
    safe_addstr(stdscr, y0, x, label, curses.A_BOLD)
    yy = y0 + 1
    if asks:
        for p, sz in reversed(asks[:n]):
            safe_addstr(stdscr, yy, x, f"A {p:.2f} x{sz:>6.0f}", curses.color_pair(1)); yy += 1
    if ask is not None and bid is not None:
        safe_addstr(stdscr, yy, x, f"~ {(ask+bid)/2:.3f} sp{ask-bid:.2f}", curses.A_DIM); yy += 1
    if bids:
        for p, sz in bids[:n]:
            safe_addstr(stdscr, yy, x, f"B {p:.2f} x{sz:>6.0f}", curses.color_pair(2)); yy += 1
    if not asks and not bids:
        safe_addstr(stdscr, yy, x, "no book", curses.color_pair(1)); yy += 1
    return yy


def draw(stdscr, market, yes_team, no_team, size, buf, armed, limit_px=0.95, force_decider=False):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    with LOCK:
        g, gts = STATE["game"], STATE["game_ts"]
        bk, bk_no, bts = STATE["book"], STATE["book_no"], STATE["book_ts"]
        hold_yes, hold_no, hts = STATE["hold_yes"], STATE["hold_no"], STATE["hold_ts"]
        cost_yes, cost_no = STATE["cost_yes"], STATE["cost_no"]
        cash = STATE["cash"]
        open_orders = STATE["open_orders"]
        missed = STATE.get("missed_polls", 0)
        worker_dead = STATE["worker_dead"]
        log = list(STATE["log"])
    y = 0
    title = f" COCKPIT  {market.get('name','?')[:w-12]} "
    safe_addstr(stdscr, y, 0, title.ljust(w - 1), curses.A_REVERSE); y += 2

    mode_str = "LIVE REAL-MONEY" if REAL else f"SIM (MODE={MODE})"
    safe_addstr(stdscr, y, 2, f"Mode: {mode_str}", curses.A_BOLD | (curses.color_pair(1) if REAL else 0)); y += 1
    if worker_dead:
        safe_addstr(stdscr, y, 2, f"!! WORKER DEAD: {worker_dead} — data is STALE, restart cockpit",
                    curses.A_BOLD | curses.color_pair(1)); y += 1
    y += 1

    # --- Game state ---
    g_age = time.time() - gts
    safe_addstr(stdscr, y, 2, "── GetTopLive ───────────────", curses.A_BOLD); y += 1
    if g is None:
        safe_addstr(stdscr, y, 2, "no live game data (game over / not found / dropped from feed)"); y += 1
    else:
        rt, dt = g.get("radiant_team") or "Radiant", g.get("dire_team") or "Dire"
        gt = g.get("game_time_sec") or 0
        lead = g.get("radiant_lead") or 0
        rs, ds = g.get("radiant_score"), g.get("dire_score")
        leader = rt if lead > 0 else (dt if lead < 0 else "even")
        safe_addstr(stdscr, y, 2, f"{rt}  vs  {dt}"); y += 1
        # flag a flickering/stale feed instead of silently disconnecting
        if g_age > 4 and not g.get("game_over"):
            safe_addstr(stdscr, y, 2, f"~ feed flickering — last update {g_age:.0f}s ago (reconnecting, {missed} missed) ~",
                        curses.A_BOLD | curses.color_pair(3)); y += 1
        safe_addstr(stdscr, y, 2, f"time {gt//60}:{gt%60:02d}   kills {rs}-{ds}   age {fmt_age(gts)}"); y += 1
        nwcol = curses.color_pair(2) if lead > 0 else (curses.color_pair(1) if lead < 0 else 0)
        safe_addstr(stdscr, y, 2, f"NET WORTH lead: {lead:+d}  → {leader}", curses.A_BOLD | nwcol); y += 1
        if g.get("game_over"):
            safe_addstr(stdscr, y, 2, "*** GAME OVER ***", curses.A_BOLD | curses.color_pair(1)); y += 1
    y += 1

    # --- Book depth: YES (left) + NO (right) ---
    asks = bk.get("asks") if bk else None
    bids = bk.get("bids") if bk else None
    ask = bk.get("best_ask") if bk else None
    bid = bk.get("best_bid") if bk else None
    asks_no = bk_no.get("asks") if bk_no else None
    bids_no = bk_no.get("bids") if bk_no else None
    stale = (time.time() - bts) > 5
    hcol = curses.color_pair(1) if (stale or not (asks or bids)) else 0
    safe_addstr(stdscr, y, 2, f"── Book DEPTH (A=ask B=bid)  age {fmt_age(bts)} ──", curses.A_BOLD | hcol); y += 1
    y_yes = render_ladder(stdscr, y, 4, f"YES={yes_team[:13]}", bk, 3)
    y_no = render_ladder(stdscr, y, 36, f"NO={no_team[:13]}", bk_no, 3)
    y = max(y_yes, y_no) + 1

    # --- MODEL win-prob (fair vs ask) + orientation guard ---
    na = bk_no.get("best_ask") if bk_no else None
    if _winprob is not None and g is not None and not g.get("game_over"):
        try:
            gt2 = int(g.get("game_time_sec") or 0)
            lead2 = int(g.get("radiant_lead") or 0)
            srt = (market.get("steam_radiant_team") or "").strip()
            yes_is_rad = (srt == yes_team) if srt else (g.get("radiant_team") == yes_team)
            yes_lead = lead2 if yes_is_rad else -lead2
            if yes_is_rad:
                ediff = _winprob.elo_diff(g.get("radiant_team_id"), g.get("dire_team_id"), g.get("radiant_team"), g.get("dire_team"))
            else:
                ediff = _winprob.elo_diff(g.get("dire_team_id"), g.get("radiant_team_id"), g.get("dire_team"), g.get("radiant_team"))
            yes_fair = _winprob.fair(yes_lead, gt2, ediff)
            no_fair = 1.0 - yes_fair
            _mt = str(market.get("market_type") or "").upper()
            _decider = _is_g3(market) or force_decider
            _series_invalid = (_mt == "MATCH_WINNER" and not _decider)
            safe_addstr(stdscr, y, 2, "── MODEL win-prob (fair vs ask) ──", curses.A_BOLD); y += 1
            if _mt == "MATCH_WINNER" and _decider:
                src = "binder" if _is_g3(market) else "manual 'd'"
                safe_addstr(stdscr, y, 2, f"  DECIDER mode ({src}) → ML == map winner, fair VALID",
                            curses.A_BOLD | curses.color_pair(2)); y += 1
            elif _series_invalid:
                safe_addstr(stdscr, y, 2, "  ! BO3 series — fair NOT valid unless decider (press 'd' if this is the deciding game)",
                            curses.A_BOLD | curses.color_pair(3)); y += 1
            for lbl, fair, a in [(f"YES={yes_team[:11]}", yes_fair, ask), (f"NO ={no_team[:11]}", no_fair, na)]:
                edge = (fair - a) if a is not None else None
                es = f"edge {edge:+.2f}" if edge is not None else "no ask"
                col = (curses.color_pair(2) | curses.A_BOLD) if (edge is not None and edge >= 0.10) else 0
                safe_addstr(stdscr, y, 2, f"{lbl:16} fair {fair:.2f}   ask {a if a is not None else '-'}   {es}", col); y += 1
            # orientation / feed-flip warning: a big net-worth leader's token can't be cheap
            if abs(yes_lead) > 5000:
                ls_ask = ask if yes_lead > 0 else na
                if ls_ask is not None and ls_ask < 0.35:
                    safe_addstr(stdscr, y, 2, "!! ORIENTATION/FEED MISMATCH — leader's token is cheap; DON'T TRUST THIS BOOK",
                                curses.A_BOLD | curses.color_pair(1)); y += 1
        except Exception:
            pass
        y += 1

    # --- Order panel ---
    # Depth-based execution preview (computed from the live YES book ladder)
    pb = plan_buy(asks, size) if asks else None
    cap = min(round((pb["worst"] or ask) + buf, 2), 0.99) if (pb and (pb["worst"] or ask)) else None
    # --- ACCOUNT panel: cash, holdings value, P&L ---
    safe_addstr(stdscr, y, 2, "── ACCOUNT ──", curses.A_BOLD); y += 1
    if REAL:
        nb = bk_no.get("best_bid") if bk_no else None
        cash_s = f"${cash:.2f}" if cash is not None else "?"
        vy = (hold_yes or 0) * float(bid) if (hold_yes and bid) else 0.0
        vn = (hold_no or 0) * float(nb) if (hold_no and nb) else 0.0
        nav = (cash or 0) + vy + vn
        safe_addstr(stdscr, y, 2, f"CASH {cash_s}     +positions ${vy+vn:.2f}  =  ${nav:.2f}",
                    curses.A_BOLD | (curses.color_pair(2) if cash else 0)); y += 1
        any_pos = False
        for lbl, sh, b, cst in [("YES " + yes_team[:10], hold_yes, bid, cost_yes),
                                ("NO  " + no_team[:10], hold_no, nb, cost_no)]:
            if sh and sh > 0.01:
                any_pos = True
                val = sh * float(b) if b else 0.0
                if cst and cst > 0.01:
                    pnl = val - cst
                    pcol = curses.color_pair(2) if pnl >= 0 else curses.color_pair(1)
                    extra = f"cost ${cst:.2f}  P&L {pnl:+.2f}"
                else:
                    pcol = 0; extra = "(pre-session)"
                safe_addstr(stdscr, y, 2, f"  {lbl:14} {sh:>6.1f}sh @ {b if b else '-'} = ${val:>6.2f}   {extra}", pcol); y += 1
        if not any_pos:
            safe_addstr(stdscr, y, 2, "  no open position on this market"); y += 1
        oo_str = f"  resting orders: {open_orders}" if open_orders else ""
        safe_addstr(stdscr, y, 2, f"  holdings age {fmt_age(hts)}{oo_str}"); y += 1
    else:
        safe_addstr(stdscr, y, 2, "(SIM mode — no real balance/positions)"); y += 1
    y += 1
    safe_addstr(stdscr, y, 2, "── Order (depth-based) ──", curses.A_BOLD); y += 1
    safe_addstr(stdscr, y, 2, f"size ${size}   cap buffer +{buf:.2f}   limit-sell @ {limit_px}"); y += 1
    if pb and pb["vwap"]:
        ft = "" if pb["full"] else f"  PARTIAL(only ${pb['spent']:.0f} in book)"
        safe_addstr(stdscr, y, 2, f"BUY YES plan: ${size} -> {pb['shares']:.1f} sh @ VWAP {pb['vwap']:.3f}  cap {cap}{ft}"); y += 1
    if bids and hold_yes and hold_yes > 0.01:
        flY = sane_sell_floor(bk, buf)
        ps = plan_dump(bids, hold_yes, flY)
        rt = "" if ps["resting"] < 0.01 else f"  {ps['resting']:.0f}sh REST@{flY}"
        safe_addstr(stdscr, y, 2, f"SELL YES plan: {hold_yes:.1f}sh -> ~${ps['proceeds']:.1f} @ VWAP {_vwfmt(ps['vwap'])}  floor {flY}{rt}"); y += 1
    pbn = plan_buy(asks_no, size) if asks_no else None
    if pbn and pbn["vwap"]:
        capn = min(round((pbn["worst"] or bk_no.get("best_ask")) + buf, 2), 0.99)
        ftn = "" if pbn["full"] else "  PARTIAL"
        safe_addstr(stdscr, y, 2, f"BUY NO  plan: ${size} -> {pbn['shares']:.1f} sh @ VWAP {pbn['vwap']:.3f}  cap {capn}{ftn}"); y += 1
    if bids_no and hold_no and hold_no > 0.01:
        flN = sane_sell_floor(bk_no, buf)
        psn = plan_dump(bids_no, hold_no, flN)
        rtn = "" if psn["resting"] < 0.01 else f"  {psn['resting']:.0f}sh REST@{flN}"
        safe_addstr(stdscr, y, 2, f"SELL NO  plan: {hold_no:.1f}sh -> ~${psn['proceeds']:.1f} @ VWAP {_vwfmt(psn['vwap'])}  floor {flN}{rtn}"); y += 1
    if armed:
        act, sd = armed.split("_")            # e.g. "buy_yes" / "cancel_all"
        team = yes_team if sd == "yes" else no_team
        RED = curses.A_BOLD | curses.color_pair(1)
        YEL = curses.A_BOLD | curses.color_pair(3)
        if act == "cancel":
            safe_addstr(stdscr, y, 2, f">>> CANCEL all {open_orders or 0} resting order(s) on this market — y/ENTER=confirm, any=cancel", YEL); y += 1
        elif act == "buy":
            _pb = pb if sd == "yes" else pbn
            safe_addstr(stdscr, y, 2, "######## CONFIRM BUY ########", YEL); y += 1
            if _pb and _pb["vwap"]:
                safe_addstr(stdscr, y, 2, f"# BUY {team} ${size} -> ~{_pb['shares']:.0f} sh @ VWAP {_pb['vwap']:.3f}", YEL); y += 1
            else:
                safe_addstr(stdscr, y, 2, f"# BUY {team} ${size} (no book — may not fill)", RED); y += 1
            safe_addstr(stdscr, y, 2, "# y / ENTER = FIRE     any other key = cancel", YEL); y += 1
        elif act == "limit":
            held = (hold_yes if sd == "yes" else hold_no) or 0.0
            cost = cost_yes if sd == "yes" else cost_no
            safe_addstr(stdscr, y, 2, "###### CONFIRM LIMIT SELL ######", YEL); y += 1
            safe_addstr(stdscr, y, 2, f"# SELL {team} {held:.1f} sh @ LIMIT {limit_px}  (rests until a buyer hits it)", YEL); y += 1
            if cost > 0:
                safe_addstr(stdscr, y, 2, f"# paid ~${cost:.2f}  =>  P&L if filled ~${held*limit_px-cost:+.2f}", YEL); y += 1
            safe_addstr(stdscr, y, 2, "# y / ENTER = FIRE     any other key = cancel", YEL); y += 1
        else:
            held = (hold_yes if sd == "yes" else hold_no) or 0.0
            _bids = bids if sd == "yes" else bids_no
            cost = cost_yes if sd == "yes" else cost_no
            _bk = bk if sd == "yes" else bk_no
            safe_addstr(stdscr, y, 2, "############ CONFIRM DUMP ############", RED); y += 1
            if _bids and held > 0.01:
                fl = sane_sell_floor(_bk, buf)
                _ps = plan_dump(_bids, held, fl)
                safe_addstr(stdscr, y, 2, f"# SELL {team} {held:.1f} sh   floor {fl}  (won't sell below)", RED); y += 1
                safe_addstr(stdscr, y, 2, f"#  -> ~{_ps['sold']:.0f}sh FILL @ VWAP {_vwfmt(_ps['vwap'])} = ~${_ps['proceeds']:.2f}", RED); y += 1
                if _ps["resting"] >= 0.01:
                    safe_addstr(stdscr, y, 2, f"#  {_ps['resting']:.0f}sh bids below floor -> RESTS (raise ']' to dump lower)", RED); y += 1
                if cost > 0:
                    safe_addstr(stdscr, y, 2, f"# paid ~${cost:.2f}  =>  P&L on fill ~${_ps['proceeds']-cost:+.2f}", RED); y += 1
            else:
                safe_addstr(stdscr, y, 2, "# no position to sell", RED); y += 1
            safe_addstr(stdscr, y, 2, "# y / ENTER = FIRE     any other key = cancel", RED); y += 1
            safe_addstr(stdscr, y, 2, "#####################################", RED); y += 1
    y += 1

    # --- Log ---
    safe_addstr(stdscr, y, 2, "── Log ──────────────────────", curses.A_BOLD); y += 1
    for line in log[-6:]:
        if y < h - 2:
            safe_addstr(stdscr, y, 2, line[:w - 4]); y += 1

    hint = "[b]buy [n]buyNO  [s]dumpYES [x]dumpNO  [l]limitYES [;]limitNO [,/.]limitpx  [c]cancel  [+/-]size  [r]refresh [q]quit"
    safe_addstr(stdscr, h - 1, 0, hint[:w - 1], curses.A_REVERSE)
    stdscr.refresh()


def cockpit(stdscr, match_id, market, yes_token, no_token, yes_team, no_team, tick, neg_risk):
    curses.curs_set(0)
    curses.start_color(); curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_RED, -1)
    curses.init_pair(2, curses.COLOR_GREEN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    stdscr.timeout(150)
    size = float(os.getenv("COCKPIT_SIZE", "50"))
    buf = 0.05
    limit_px = 0.95   # limit-sell target (resting GTC sell, e.g. take-profit)
    armed = None
    force_decider = False   # 'd' toggles: treat a MATCH_WINNER as the game-3 decider
    while True:
        draw(stdscr, market, yes_team, no_team, size, buf, armed, limit_px, force_decider)
        try:
            c = stdscr.getch()
        except KeyboardInterrupt:
            break
        if c == -1:
            continue
        if armed:
            if c in (ord("y"), ord("Y"), curses.KEY_ENTER, 10, 13):
                act, sd = armed.split("_")
                if act == "cancel":
                    with LOCK:
                        STATE["order_request"] = {"action": "cancel"}
                    logmsg("submitted CANCEL open orders")
                elif act == "buy":
                    with LOCK:
                        STATE["order_request"] = {"action": "buy", "token_side": sd, "amount": size, "buf": buf}
                    logmsg(f"submitted BUY {sd.upper()} ${size} (depth-priced)")
                elif act == "limit":
                    with LOCK:
                        STATE["order_request"] = {"action": "limit_sell", "token_side": sd, "price": limit_px}
                    logmsg(f"submitted LIMIT SELL {sd.upper()} @ {limit_px} (rests)")
                else:
                    with LOCK:
                        STATE["order_request"] = {"action": "sell", "token_side": sd, "buf": buf}
                    logmsg(f"submitted SELL {sd.upper()} (depth-priced dump)")
            else:
                logmsg("cancelled")
            armed = None
            continue
        if c in (ord("q"), ord("Q")):
            break
        elif c == ord("b"):
            armed = "buy_yes"
        elif c == ord("n"):
            armed = "buy_no"
        elif c == ord("s"):
            armed = "sell_yes"
        elif c == ord("x"):
            armed = "sell_no"
        elif c == ord("c"):
            armed = "cancel_all"
        elif c == ord("l"):
            armed = "limit_yes"          # limit-sell YES position @ limit_px
        elif c == ord(";"):
            armed = "limit_no"           # limit-sell NO position @ limit_px
        elif c == ord(".") or c == ord(">"):
            limit_px = min(0.99, round(limit_px + 0.01, 2))
        elif c == ord(",") or c == ord("<"):
            limit_px = max(0.01, round(limit_px - 0.01, 2))
        elif c == ord("+") or c == ord("="):
            size += 5
        elif c == ord("-") or c == ord("_"):
            size = max(1, size - 5)
        elif c == ord("]"):
            buf = min(0.20, round(buf + 0.01, 2))
        elif c == ord("["):
            buf = max(0.0, round(buf - 0.01, 2))
        elif c == ord("d"):
            force_decider = not force_decider
            logmsg(f"decider override {'ON (ML=map3, fair valid)' if force_decider else 'OFF'}")
        # r / anything else just refreshes


def main():
    markets = load_markets()
    query = sys.argv[1].lower() if len(sys.argv) > 1 else None

    pairs = asyncio.run(list_live_mapped(markets))
    if not pairs:
        print("No live mapped matches right now.")
        return

    chosen = None
    if query:
        # word-based match: ALL query words must appear (any order), so
        # "modus vs inner circle" matches teams "MODUS" / "Inner Circle x Insanity"
        # and market "Inner Circle vs MODUS". Ignore filler words.
        _stop = {"vs", "v", "dota", "2", "the", "-"}
        qwords = [w for w in query.split() if w not in _stop]
        for g, m in pairs:
            hay = f"{g.get('radiant_team')} {g.get('dire_team')} {g.get('match_id')} {m.get('name')}".lower()
            if (query in hay) or (qwords and all(w in hay for w in qwords)):
                chosen = (g, m); break
        if not chosen:
            print(f"No live match matching '{query}'.")
            query = None
    if not chosen:
        print("Live mapped matches:")
        for i, (g, m) in enumerate(pairs):
            gt = g.get("game_time_sec") or 0
            short = market_tag(m)[1]
            print(f"  [{i}] {short:8}  {g.get('radiant_team')} vs {g.get('dire_team')}  "
                  f"({gt//60}:{gt%60:02d})  → {m.get('name')}")
        try:
            idx = int(input("Pick #: ").strip())
            chosen = pairs[idx]
        except (ValueError, IndexError, KeyboardInterrupt):
            print("aborted"); return

    g, market = chosen
    match_id = str(g.get("match_id"))
    yes_token = str(market["yes_token_id"])
    no_token = str(market["no_token_id"])
    yes_team = market.get("yes_team") or "YES"
    no_team = market.get("no_team") or "NO"
    tick = market.get("tick_size", "0.01")
    neg_risk = bool(market.get("neg_risk", False))

    print(f"\nOpening cockpit: {market.get('name')}")
    print(f"  >>> {market_tag(market)[2]} <<<")
    print(f"YES={yes_team}  NO={no_team}  match={match_id}  mode={'LIVE REAL' if REAL else MODE}")
    print("Order prints/errors go to logs/cockpit.log. Launching in 1s...")
    time.sleep(1)

    t = threading.Thread(target=worker, args=(match_id, market, yes_token, no_token, tick, neg_risk), daemon=True)
    t.start()

    # Redirect noisy stdout (book_refresh prints) to a file so curses stays clean.
    os.makedirs("logs", exist_ok=True)
    _real_out = sys.stdout
    logf = open("logs/cockpit.log", "a")
    sys.stdout = logf
    try:
        curses.wrapper(cockpit, match_id, market, yes_token, no_token, yes_team, no_team, tick, neg_risk)
    finally:
        STATE["stop"] = True
        sys.stdout = _real_out
        logf.close()
        print("cockpit closed.")


if __name__ == "__main__":
    main()
