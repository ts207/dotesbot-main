import asyncio
import os
from py_clob_client_v2 import ClobClient, ApiCreds, BalanceAllowanceParams, AssetType, SignatureTypeV2
from dotenv import load_dotenv

load_dotenv()

async def main():
    host = os.getenv("POLY_CLOB_HOST", "https://clob.polymarket.com")
    chain_id = int(os.getenv("POLY_CHAIN_ID", "137"))
    private_key = os.getenv("POLY_PRIVATE_KEY")
    funder = os.getenv("POLY_FUNDER_ADDRESS")
    
    creds = ApiCreds(
        api_key=os.getenv("POLY_CLOB_API_KEY"),
        api_secret=os.getenv("POLY_CLOB_SECRET"),
        api_passphrase=os.getenv("POLY_CLOB_PASS_PHRASE"),
    )
    
    # SigType 2 (POLY_1271) is used for Proxy/Deposit wallets (the funder address)
    client = ClobClient(host, chain_id, private_key, creds, signature_type=SignatureTypeV2.POLY_1271, funder=funder)
    
    print(f"Attempting to sync CLOB cash balance for funder: {funder}")
    
    try:
        # Sync collateral (USDC.e)
        params = BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            signature_type=SignatureTypeV2.POLY_1271
        )
        
        # This call tells the Polymarket CLOB to check the on-chain balance and update its internal records
        resp = await asyncio.to_thread(client.update_balance_allowance, params)
        print(f"Sync Balance Response: {resp}")
        
        # Fetch the updated balance
        bal_resp = await asyncio.to_thread(client.get_balance_allowance, params)
        balance = float(bal_resp.get("balance", 0)) / 1e6
        print(f"\nSUCCESS! Updated CLOB Cash Balance: ${balance:.2f}")
        
    except Exception as e:
        print(f"Error syncing balance: {e}")

if __name__ == "__main__":
    asyncio.run(main())
