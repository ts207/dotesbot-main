import os
from dotenv import load_dotenv
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import ApiCreds

load_dotenv()

host = "https://clob.polymarket.com"
chain_id = 137
private_key = os.getenv("POLY_PRIVATE_KEY")
creds = ApiCreds(
    api_key=os.getenv("POLY_CLOB_API_KEY"),
    api_secret=os.getenv("POLY_CLOB_SECRET"),
    api_passphrase=os.getenv("POLY_CLOB_PASS_PHRASE"),
)

client = ClobClient(
    host=host,
    chain_id=chain_id,
    key=private_key,
    creds=creds,
)

print(f"EOA Address: {client.get_address()}")
try:
    print(f"Proxy Wallet: {client.get_proxy_address()}")
except Exception as e:
    print(f"Could not get proxy: {e}")

try:
    print(f"Expected Deposit Wallet: {client.get_expected_deposit_wallet()}")
except Exception as e:
    print(f"Could not get expected deposit wallet: {e}")
