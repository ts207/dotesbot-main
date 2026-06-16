#!/usr/bin/env python3
"""Bot health + risk monitor. Run periodically (cron / supervisor / `python3 monitor.py`).

Each run it:
  1. snapshots NAV (cash + token value at bid) -> logs/nav_history.csv  (equity curve)
  2. runs health + risk checks (heartbeat, order errors, drawdown, over-concentration,
     settled-but-unredeemed positions)
  3. prints a one-screen status + any ALERTS, appends to logs/monitor.log
  4. exit code: 0 = OK, 1 = WARN, 2 = CRITICAL

It only OBSERVES and (optionally, with --halt-on-critical) flips
ENABLE_REAL_LIVE_TRADING=false in an emergency. It NEVER opens/sizes/picks trades.
"""
import asyncio, csv, json, os, sys, time
from datetime import datetime, timezone

import aiohttp
import cockpit
try:
    from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams
except ModuleNotFoundError:
    AssetType = None
    BalanceAllowanceParams = None

HEARTBEAT_MAX_S = 240          # bot considered hung past this
ERR_RATE_WARN = 0.5            # >50% of recent order attempts erroring
DRAWDOWN_KILL = float(os.getenv("MAX_DAILY_DRAWDOWN_USD", "50"))
PER_MATCH_WARN = float(os.getenv("VALUE_MAX_PER_MATCH", "6")) * 2.5  # over-concentration
NAV_HIST = "logs/nav_history.csv"
MON_LOG = "logs/monitor.log"


def _heartbeat_age():
    try:
        return time.time() - float(open("logs/heartbeat").read().strip())
    except Exception:
        return None


def _proc_alive(pat):
    return os.system(f"pgrep -f '{pat}' >/dev/null 2>&1") == 0


async def run(halt_on_critical=False):
    alerts = []  # (level, msg)  level: WARN|CRIT

    # --- 1. NAV snapshot ---
    real_live = os.getenv("ENABLE_REAL_LIVE_TRADING", "false").lower() in {"1", "true", "yes"}
    live_nav_available = real_live and AssetType is not None and BalanceAllowanceParams is not None
    cash = 0.0
    token_val = 0.0
    over = {}   # match_id -> exposure
    stuck = []  # settled-but-held
    position_path = "logs/live_positions.json" if real_live else "logs/paper_positions_v2.json"
    try:
        raw_positions = json.load(open(position_path))
        positions = raw_positions.get("positions", raw_positions if isinstance(raw_positions, list) else [])
    except Exception:
        positions = []
    openp = [p for p in positions if p.get("state") == "OPEN"]
    if live_nav_available:
        c = cockpit.make_client()
        cash = float((c.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)).get("balance") or 0)) / 1e6
        async with aiohttp.ClientSession() as s:
            seen = set()
            for p in openp:
                tok = str(p.get("token_id") or "")
                if not tok or tok in seen:
                    continue
                seen.add(tok)
                try:
                    sh = cockpit.get_shares(c, tok)
                except Exception:
                    sh = 0.0
                if sh < 0.1:
                    continue
                b = await cockpit.fetch_depth(s, tok)
                bid = (b.get("best_bid") if b else None)
                mid = str(p.get("match_id") or "")
                if bid is None:
                    stuck.append((p.get("market_name", "")[:36], sh))   # no book = settled/illiquid
                    continue
                v = sh * float(bid)
                token_val += v
                over[mid] = over.get(mid, 0.0) + v
    elif real_live:
        alerts.append(("WARN", "live NAV unavailable; install requirements-live.txt for wallet monitoring"))
    nav = cash + token_val

    # append equity curve
    newf = not os.path.exists(NAV_HIST)
    prev = None
    if not newf:
        try:
            rows = list(csv.DictReader(open(NAV_HIST)))
            if rows:
                prev = float(rows[-1]["nav"])
        except Exception:
            pass
    with open(NAV_HIST, "a", newline="") as f:
        w = csv.writer(f)
        if newf:
            w.writerow(["ts", "cash", "token_value", "nav"])
        w.writerow([datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    round(cash, 2), round(token_val, 2), round(nav, 2)])

    # --- 2. checks ---
    hb = _heartbeat_age()
    if hb is None or hb > HEARTBEAT_MAX_S:
        alerts.append(("CRIT", f"bot heartbeat {hb if hb is None else round(hb)}s (>{HEARTBEAT_MAX_S}s or missing) — hung/dead"))
    if not _proc_alive("main.py"):
        alerts.append(("CRIT", "main.py process not running"))
    if not _proc_alive("supervisor.py"):
        alerts.append(("CRIT", "supervisor not running — no auto-restart"))

    # order error rate (last 40 live attempts)
    try:
        la = [r for r in csv.DictReader(open("logs/live_attempts.csv"))][-40:]
        if la:
            errs = sum(1 for r in la if r.get("order_status") == "exception")
            if errs / len(la) > ERR_RATE_WARN:
                alerts.append(("WARN", f"order error rate {errs}/{len(la)} in last 40 — exchange/feed degraded"))
    except Exception:
        pass

    # daily drawdown vs session start. "Session" resets after a deposit (a >$30
    # jump up) so funding the wallet doesn't read as a phantom gain that masks
    # later losses, and a fresh deposit re-baselines the kill threshold.
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rows = [r for r in csv.DictReader(open(NAV_HIST)) if r["ts"].startswith(today)]
        if rows:
            day_start = float(rows[0]["nav"])
            for i in range(1, len(rows)):
                if float(rows[i]["nav"]) - float(rows[i - 1]["nav"]) > 30:  # deposit
                    day_start = float(rows[i]["nav"])
            dd = nav - day_start
            if dd < -DRAWDOWN_KILL:
                alerts.append(("CRIT", f"daily drawdown ${dd:.2f} exceeds kill ${DRAWDOWN_KILL:.0f}"))
            elif dd < -DRAWDOWN_KILL * 0.6:
                alerts.append(("WARN", f"daily drawdown ${dd:.2f} approaching kill ${DRAWDOWN_KILL:.0f}"))
    except Exception:
        pass

    # over-concentration
    for mid, v in over.items():
        if v > PER_MATCH_WARN:
            alerts.append(("WARN", f"match {mid} exposure ${v:.2f} > ${PER_MATCH_WARN:.0f} (over-concentration)"))
    # settled-but-unredeemed
    if stuck:
        alerts.append(("WARN", f"{len(stuck)} position(s) with no book (settled/illiquid — may need redeem): " +
                       ", ".join(f"{n}({sh:.0f}sh)" for n, sh in stuck[:4])))

    # --- 3. report ---
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    dnav = f"  Δ{nav-prev:+.2f}" if prev is not None else ""
    crit = [m for lv, m in alerts if lv == "CRIT"]
    warn = [m for lv, m in alerts if lv == "WARN"]
    status = "CRITICAL" if crit else ("WARN" if warn else "OK")
    line = f"[{ts}] {status}  NAV ${nav:.2f} (cash ${cash:.2f} + pos ${token_val:.2f}){dnav}  hb {round(hb) if hb else '?'}s  open {len(openp)}"
    print(line)
    for lv, m in alerts:
        print(f"   !! {lv}: {m}")
    with open(MON_LOG, "a") as f:
        f.write(line + "\n")
        for lv, m in alerts:
            f.write(f"   !! {lv}: {m}\n")

    if crit and halt_on_critical:
        print("   >>> CRITICAL + --halt-on-critical: would set ENABLE_REAL_LIVE_TRADING=false (manual confirm recommended)")

    return 2 if crit else (1 if warn else 0)


def _hb():
    try:
        open("logs/monitor_heartbeat", "w").write(str(time.time()))
    except Exception:
        pass


if __name__ == "__main__":
    halt = "--halt-on-critical" in sys.argv
    if "--loop" in sys.argv:
        interval = int(os.getenv("MONITOR_INTERVAL_SEC", "300"))  # deterministic watch cadence
        while True:
            _hb()
            try:
                asyncio.run(run(halt_on_critical=halt))
            except Exception as e:
                print(f"[monitor] pass error: {e}")
            time.sleep(interval)
    else:
        rc = asyncio.run(run(halt_on_critical=halt))
        sys.exit(rc)
