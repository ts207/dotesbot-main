#!/usr/bin/env python3
"""Trustworthy NAV (net-asset-value) tracker — sidesteps the unreliable CLOB
trade feed entirely. NAV = USDC cash + every held conditional token marked to its
current book bid (on-chain ground truth). Appends a snapshot to logs/nav_history.csv
and prints the change since the last snapshot. P&L = change in NAV (minus deposits).

Run periodically (manually, cron, or /loop) for a trustworthy equity curve.
Usage: python3 nav.py
"""
import asyncio, aiohttp, csv, json, os
from datetime import datetime, timezone
import cockpit

HIST = "logs/nav_history.csv"


async def main():
    c = cockpit.make_client()
    # 1) USDC cash
    from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams
    r = c.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    cash = float((r.get("balance") if isinstance(r, dict) else 0) or 0) / 1e6

    # 2) value held tokens (from the position store) at current bid
    try:
        from storage_v2 import StorageV2
        positions = StorageV2().load_positions(mode="live")
    except Exception:
        positions = []
    seen = set()
    token_value = 0.0
    held = []
    async with aiohttp.ClientSession() as s:
        for p in positions:
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
            bid = (b.get("best_bid") if b else None) or 0.0
            val = sh * float(bid)
            token_value += val
            held.append((p.get("market_name", "")[:40], round(sh, 1), bid, round(val, 2)))

    nav = cash + token_value
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 3) prior snapshot for delta
    prev = None
    if os.path.exists(HIST):
        try:
            rows = list(csv.DictReader(open(HIST)))
            if rows:
                prev = float(rows[-1]["nav"])
        except Exception:
            pass

    # 4) append
    newfile = not os.path.exists(HIST)
    with open(HIST, "a", newline="") as f:
        w = csv.writer(f)
        if newfile:
            w.writerow(["ts", "cash", "token_value", "nav"])
        w.writerow([ts, round(cash, 2), round(token_value, 2), round(nav, 2)])

    # 5) report
    print(f"NAV snapshot @ {ts}")
    print(f"  cash (USDC):     ${cash:.2f}")
    print(f"  open positions:  ${token_value:.2f}")
    for nm, sh, bid, val in held:
        print(f"     {nm:40} {sh:>7} sh @ {bid} = ${val}")
    print(f"  ── NAV (total):  ${nav:.2f}")
    if prev is not None:
        print(f"  Δ since last snapshot: ${nav - prev:+.2f}")


if __name__ == "__main__":
    asyncio.run(main())
