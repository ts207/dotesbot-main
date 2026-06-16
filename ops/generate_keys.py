import os
from dotenv import load_dotenv, set_key
from py_clob_client_v2.client import ClobClient

load_dotenv()
env_path = ".env"

host = "https://clob.polymarket.com"
chain_id = 137
private_key = os.getenv("POLY_PRIVATE_KEY") or os.getenv("PK")
funder = os.getenv("POLY_FUNDER_ADDRESS")

print(f"Connecting to Polymarket to generate API Keys for Deposit Wallet: {funder}")

client = ClobClient(
    host=host,
    chain_id=chain_id,
    key=private_key,
    signature_type=3,  # POLY_1271 (Deposit Wallet Flow)
    funder=funder
)

try:
    # Generate new API credentials associated with the Deposit Wallet
    creds = client.create_or_derive_api_key()
    print("\n✅ Successfully generated new API Keys!")
    print(f"Key: {creds.api_key}")
    
    # Save them back into .env
    set_key(env_path, "POLY_CLOB_API_KEY", creds.api_key)
    set_key(env_path, "POLY_CLOB_SECRET", creds.api_secret)
    set_key(env_path, "POLY_CLOB_PASS_PHRASE", creds.api_passphrase)
    set_key(env_path, "POLY_SIGNATURE_TYPE", "3")
    
    print("\n✅ Successfully updated .env with the new credentials!")
    print("You can now run 'python3 test_order.py' again.")
    
except Exception as e:
    print(f"\n❌ Error generating keys: {e}")
