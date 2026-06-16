import os
from dotenv import load_dotenv, set_key
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import ApiCreds

def main():
    print("Starting Polymarket Deposit Wallet Migration...")
    load_dotenv()
    
    env_path = ".env"
    host = os.getenv("POLY_CLOB_HOST", "https://clob.polymarket.com")
    chain_id = int(os.getenv("POLY_CHAIN_ID", "137"))
    private_key = os.getenv("POLY_PRIVATE_KEY") or os.getenv("PK")
    
    if not private_key:
        print("Error: Missing POLY_PRIVATE_KEY in .env")
        return
        
    creds = ApiCreds(
        api_key=os.getenv("POLY_CLOB_API_KEY") or os.getenv("CLOB_API_KEY"),
        api_secret=os.getenv("POLY_CLOB_SECRET") or os.getenv("CLOB_SECRET"),
        api_passphrase=os.getenv("POLY_CLOB_PASS_PHRASE") or os.getenv("CLOB_PASS_PHRASE"),
    )

    # 1. Initialize temporary client with EOA (signature_type 1 or 2)
    # We use signature_type 2 as base to just fetch the expected wallet
    temp_client = ClobClient(
        host=host,
        chain_id=chain_id,
        key=private_key,
        creds=creds,
        signature_type=2,
    )
    
    owner_address = temp_client.get_address()
    print(f"Owner EOA Address: {owner_address}")
    
    # 2. Derive Deposit Wallet
    try:
        deposit_wallet = temp_client.get_expected_deposit_wallet()
        print(f"\n=> Your Deposit Wallet Address is: {deposit_wallet}")
    except Exception as e:
        print(f"Failed to derive deposit wallet. Are you on the latest py_clob_client_v2? Error: {e}")
        # Fallback to proxy
        try:
            deposit_wallet = temp_client.get_proxy_address()
            print(f"\n=> Falling back to Proxy Address: {deposit_wallet}")
        except Exception as fallback_e:
            print(f"Failed to get proxy address: {fallback_e}")
            return
            
    # Update .env
    print("\nUpdating .env file with POLY_SIGNATURE_TYPE=3 and POLY_FUNDER_ADDRESS...")
    set_key(env_path, "POLY_SIGNATURE_TYPE", "3")
    set_key(env_path, "POLY_FUNDER_ADDRESS", deposit_wallet)
    
    # 3. Initialize new client with deposit wallet to sync balance
    print("Syncing CLOB balances using signature_type=3...")
    try:
        final_client = ClobClient(
            host=host,
            chain_id=chain_id,
            key=private_key,
            creds=creds,
            signature_type=3,
            funder=deposit_wallet
        )
        # Calling update_balance_allowance. For USDC (ERC20), type is usually required.
        # We try basic signature
        final_client.update_balance_allowance({"asset_type": "ERC20"})
        print("Successfully synced balances!")
    except Exception as e:
        print(f"Note: Could not automatically sync balances (you may need to fund the deposit wallet first): {e}")
        
    print("\nMigration Complete! Your bot will now use the deposit wallet flow (POLY_1271).")
    print(f"IMPORTANT: You MUST send USDC to {deposit_wallet} to trade. Funds in your EOA will not be used.")
    print("You can restart the bot now.")

if __name__ == "__main__":
    main()
