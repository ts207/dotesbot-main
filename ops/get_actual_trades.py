import os, asyncio
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
    client = ClobClient(host, chain_id, private_key, creds)
    
    trades = await asyncio.to_thread(client.get_trades)
    print(f"Retrieved {len(trades)} trades.")
    
    buy_vol = 0
    sell_vol = 0
    
    for t in trades:
        side = t.get('side')
        size = float(t.get('size'))
        price = float(t.get('price'))
        match_time = t.get('match_time')
        outcome = t.get('outcome')
        total = size * price
        
        if side == 'BUY':
            buy_vol += total
        else:
            sell_vol += total
            
        print(f"[{match_time}] {side} {size} @ {price} = ${total:.2f} ({outcome})")
        
    print(f"\nSummary:")
    print(f"Total Buy Volume:  ${buy_vol:.2f}")
    print(f"Total Sell Volume: ${sell_vol:.2f}")
    print(f"Net Cashflow:      ${sell_vol - buy_vol:.2f}")

if __name__ == "__main__":
    asyncio.run(main())
