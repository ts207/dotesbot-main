import asyncio
import os
import json
import yaml
from live_executor import LiveCLOBClient
from dotenv import load_dotenv
from py_clob_client_v2 import BalanceAllowanceParams, AssetType

load_dotenv()

async def check_all_balances():
    client = LiveCLOBClient()
    print(f"Funder: {os.getenv('POLY_FUNDER_ADDRESS')}")
    
    # Load markets to get token IDs
    with open("markets.yaml", "r") as f:
        data = yaml.safe_load(f)
    
    markets_list = data.get("markets", [])
    
    token_ids = set()
    for m in markets_list:
        yid = m.get("yes_token_id")
        nid = m.get("no_token_id")
        if yid and "TOKEN_ID_HERE" not in str(yid):
            token_ids.add(str(yid))
        if nid and "TOKEN_ID_HERE" not in str(nid):
            token_ids.add(str(nid))
            
    print(f"Checking {len(token_ids)} unique tokens...")
    
    # In V2, get_balance_allowance takes token_id in params
    # But wait, AssetType.CONDITIONAL might be what we need for CTF tokens.
    
    results = []
    sem = asyncio.Semaphore(10)
    
    async def get_bal(tid):
        async with sem:
            try:
                # Based on some docs, for conditional tokens we use token_id
                params = BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=tid)
                resp = await asyncio.to_thread(client._client.get_balance_allowance, params)
                # resp is likely a dict with "balance"
                bal = resp.get("balance")
                if bal and float(bal) > 0:
                    return tid, bal
            except:
                pass
        return None, None

    tasks = [get_bal(tid) for tid in token_ids]
    for coro in asyncio.as_completed(tasks):
        tid, bal = await coro
        if tid:
            print(f"Token {tid}: {bal}")
            results.append({"token_id": tid, "balance": bal})
            
    # Also check COLLATERAL (USDC)
    try:
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        resp = await asyncio.to_thread(client._client.get_balance_allowance, params)
        bal = resp.get('balance')
        if bal:
            print(f"USDC Balance: {float(bal)/1e6:.6f}")
    except Exception as e:
        print(f"Error checking USDC: {e}")

    if not results:
        print("No non-zero token balances found.")
    else:
        print(f"Found {len(results)} non-zero balances.")

if __name__ == "__main__":
    asyncio.run(check_all_balances())
