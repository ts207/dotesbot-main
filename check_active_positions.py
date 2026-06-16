import asyncio
import os
from py_clob_client_v2 import ClobClient, ApiCreds, BalanceAllowanceParams, AssetType
from dotenv import load_dotenv

load_dotenv()

async def main():
    host = os.getenv("POLY_CLOB_HOST", "https://clob.polymarket.com")
    chain_id = int(os.getenv("POLY_CHAIN_ID", "137"))
    private_key = os.getenv("POLY_PRIVATE_KEY")
    creds = ApiCreds(
        api_key=os.getenv("POLY_CLOB_API_KEY"),
        api_secret=os.getenv("POLY_CLOB_SECRET"),
        api_passphrase=os.getenv("POLY_CLOB_PASS_PHRASE"),
    )
    client = ClobClient(host, chain_id, private_key, creds)
    
    trades = await asyncio.to_thread(client.get_trades)
    
    # Track unique assets and their details
    assets = {}
    for t in trades:
        aid = t.get('asset_id')
        if aid not in assets:
            assets[aid] = {'outcome': t.get('outcome'), 'side': t.get('side')}
    
    print(f"Checking share balances for {len(assets)} active markets...\n")
    
    any_held = False
    for aid, info in assets.items():
        try:
            params = BalanceAllowanceParams(asset_type='CONDITIONAL', token_id=aid)
            resp = await asyncio.to_thread(client.get_balance_allowance, params)
            bal = float(resp.get('balance', 0))
            if bal > 0:
                shares = bal / 1e6
                print(f"HOLDING: {shares:.4f} shares of {info['outcome']} ({info['side']}) [ID: {aid}]")
                any_held = True
        except:
            pass
            
    if not any_held:
        print("No active share positions found. All trades appear fully exited.")

if __name__ == "__main__":
    asyncio.run(main())
