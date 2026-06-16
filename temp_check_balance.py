import asyncio
import os
from py_clob_client_v2 import ClobClient, ApiCreds, BalanceAllowanceParams, AssetType
from dotenv import load_dotenv

load_dotenv()

async def main():
    host = os.getenv("POLY_CLOB_HOST")
    chain_id = int(os.getenv("POLY_CHAIN_ID"))
    private_key = os.getenv("POLY_PRIVATE_KEY")
    creds = ApiCreds(
        api_key=os.getenv("POLY_CLOB_API_KEY"),
        api_secret=os.getenv("POLY_CLOB_SECRET"),
        api_passphrase=os.getenv("POLY_CLOB_PASS_PHRASE"),
    )
    client = ClobClient(host, chain_id, private_key, creds, signature_type=3, funder=os.getenv("POLY_FUNDER_ADDRESS"))
    
    # Check USDC
    usdc = await asyncio.to_thread(client.get_balance_allowance, BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print(f"USDC: {usdc}")
    
    # Check the known non-zero tokens
    tokens = {
        "PARIVISION_NO": '54928042593149704965267612769715746686316530740494697138501900383026479423585',
        "REKONIX_NO": '40742574532154267962415437874954114143388197628912868162733853719854993144219'
    }
    for name, tid in tokens.items():
        tok_bal = await asyncio.to_thread(client.get_balance_allowance, BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=tid))
        print(f"{name}: {tok_bal}")

if __name__ == "__main__":
    asyncio.run(main())
