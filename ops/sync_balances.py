import asyncio
import os
import json
import yaml
from live_executor import LiveCLOBClient
from live_position_store import LivePositionStore, LivePosition
from dotenv import load_dotenv
from py_clob_client_v2 import BalanceAllowanceParams, AssetType
import time

load_dotenv()

async def sync_positions():
    client = LiveCLOBClient()
    store = LivePositionStore()
    
    print("Fetching actual balances from Polymarket...")
    
    # Get all markets to map asset_id to market_name
    with open("markets.yaml", "r") as f:
        data = yaml.safe_load(f)
    
    markets_list = data.get("markets", [])
    token_to_market = {}
    for m in markets_list:
        yid = m.get("yes_token_id")
        nid = m.get("no_token_id")
        if yid: token_to_market[str(yid)] = (m.get("name"), "YES", m.get("dota_match_id"))
        if nid: token_to_market[str(nid)] = (m.get("name"), "NO", m.get("dota_match_id"))

    # Fetch all non-zero balances
    found_tokens = []
    print(f"Checking {len(token_to_market)} tokens for non-zero balances...")
    for tid in token_to_market.keys():
        try:
            params = BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=tid)
            resp = await asyncio.to_thread(client._client.get_balance_allowance, params)
            bal = float(resp.get("balance") or 0)
            if bal > 0:
                shares = bal / 1e6
                found_tokens.append((tid, shares))
        except Exception as e:
            # Polymarket API can return 400 for valid but inactive/exhausted tokens in some environments
            # Or if the token doesn't exist on the specific chain host.
            # print(f"  Error checking token {tid}: {e}")
            pass

    print(f"Found {len(found_tokens)} tokens with non-zero balance.")
    
    # Update store
    # First, mark all currently OPEN positions as CLOSED if they have no balance
    active_in_store = {p.token_id: p for p in store.positions.values() if p.state in {"OPEN", "PARTIALLY_EXITED", "PENDING_ENTRY"}}
    
    for tid, actual_shares in found_tokens:
        market_name, side, match_id = token_to_market[tid]
        if tid in active_in_store:
            pos = active_in_store[tid]
            print(f"Syncing existing position: {market_name} ({side}). Actual shares: {actual_shares:.4f}, Store shares: {pos.shares:.4f}")
            pos.shares = actual_shares
            if actual_shares < 0.01: # Dust
                print(f"  Treating {actual_shares:.4f} as dust, closing.")
                pos.state = "CLOSED"
            else:
                pos.state = "OPEN"
        else:
            print(f"Found untracked position: {market_name} ({side}). Actual shares: {actual_shares:.4f}")
            # Skip tokens from resolved markets — the orderbook disappears on resolution.
            # Shares in resolved losing markets are worthless dust; creating a position
            # would trigger infinite failed exit attempts.
            try:
                book = await asyncio.to_thread(client._client.get_order_book, tid)
                if not book or not book.get("bids") and not book.get("asks"):
                    print(f"  Market has no orderbook (resolved) — skipping.")
                    continue
            except Exception:
                print(f"  Market orderbook unavailable (resolved) — skipping.")
                continue
            if actual_shares > 0.01:
                # Create a new position record
                pos_id = f"sync_{match_id}_{tid}_{int(time.time())}"
                new_pos = LivePosition(
                    position_id=pos_id,
                    state="OPEN",
                    token_id=tid,
                    opposing_token_id="", # Unknown
                    match_id=match_id or "unknown",
                    market_name=market_name,
                    side=side,
                    entry_price=0.5, # Placeholder
                    shares=actual_shares,
                    cost_usd=actual_shares * 0.5,
                    entry_time_ns=time.time_ns(),
                    entry_game_time_sec=0,
                    event_type="SYNC_RECOVERY",
                    expected_move=0.0,
                    fair_price=0.5
                )
                store.add(new_pos)

    # Check for positions in store that have NO actual balance
    found_token_ids = {tid for tid, _ in found_tokens}
    for tid, pos in active_in_store.items():
        if tid not in found_token_ids:
            print(f"Position in store has NO balance on exchange: {pos.market_name} ({pos.side}). Marking CLOSED.")
            pos.state = "CLOSED"

    store.save()
    print("Sync complete.")

if __name__ == "__main__":
    asyncio.run(sync_positions())
