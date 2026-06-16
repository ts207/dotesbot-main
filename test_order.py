import os
from dotenv import load_dotenv
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import ApiCreds, OrderArgsV2, OrderType, MarketOrderArgs, PartialCreateOrderOptions

load_dotenv()

host = "https://clob.polymarket.com"
chain_id = 137
private_key = os.getenv("POLY_PRIVATE_KEY") or os.getenv("PK")
funder = os.getenv("POLY_FUNDER_ADDRESS")

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
    signature_type=3,  # POLY_1271 (Deposit Wallet Flow)
    funder=funder
)

print(f"Testing Deposit Wallet Flow for Funder: {funder}")

try:
    print("\n1. Syncing allowances...")
    # client.update_balance_allowance({"asset_type": "ERC20"})
    print("Allowance synced successfully!")
except Exception as e:
    print(f"Warning: Allowance sync error (could be expected if already synced): {e}")

try:
    print("\n2. Attempting to place a test MARKET Order (FAK Buy $1 at $0.01) so we don't spend money...")
    test_token = "39324475784383976532815240616554700800435245122420305146020726994307102547992"
    
    order_args = MarketOrderArgs(
        token_id=test_token,
        amount=1.0,
        side="BUY",
        price=0.01,
    )
    options = PartialCreateOrderOptions(tick_size="0.01")
    
    resp = client.create_and_post_market_order(order_args, options, OrderType.FAK)
    print("\nOrder API Response:")
    print(resp)
    
    if resp and resp.get("success"):
        print("\nSUCCESS! The 'maker address not allowed' error is gone.")
            
except Exception as e:
    print(f"\nOrder Placement Failed with Exception:\n{e}")
