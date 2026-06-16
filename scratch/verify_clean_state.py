import asyncio
import os
from py_clob_client_v2 import ClobClient, ApiCreds
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
    
    funder = os.getenv("POLY_FUNDER_ADDRESS")
    client = ClobClient(host, chain_id, private_key, creds, signature_type=2, funder=funder)
    
    print(f"Checking state for address: {funder}")
    
    try:
        # Check open orders
        orders = await asyncio.to_thread(client.get_open_orders)
        print("\n--- OPEN ORDERS ---")
        if not orders:
            print("No open orders found.")
        else:
            for o in orders:
                print(f"ID: {o.get('id')} | Market: {o.get('market')} | Side: {o.get('side')} | Size: {o.get('size')} | Price: {o.get('price')}")
    except Exception as e:
        print(f"Error fetching orders: {e}")
        
    try:
        # Check positions (we will just query the balance endpoint for non-collateral assets if possible, or use live_positions.json)
        # The easiest way to see if there is exposure is to check if we hold any token shares
        print("\n--- CHECKING FOR EXPOSURE ---")
        import json
        live_pos_path = "logs/live_positions.json"
        if os.path.exists(live_pos_path):
            with open(live_pos_path, "r") as f:
                pos_data = json.load(f)
            active = [p for p in pos_data.get("positions", []) if p.get("state") not in {"CLOSED"}]
            if not active:
                print("No active positions tracked in local state.")
            else:
                for p in active:
                    print(f"Active Position: {p.get('market_name')} | Side: {p.get('side')} | Shares: {p.get('shares')}")
        else:
            print("No local position state found.")
            
    except Exception as e:
        print(f"Error checking exposure: {e}")

if __name__ == "__main__":
    asyncio.run(main())
