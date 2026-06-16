import asyncio
import json
import websockets
import time

async def test_ws():
    url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    # Tundra vs NaVi Game 1 YES token from my earlier check
    asset_id = "39324475784383976532815240616554700800435245122420305146020726994307102547992"
    
    print(f"Connecting to {url}...")
    try:
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            sub_msg = {"assets_ids": [asset_id], "type": "market"}
            print(f"Sending sub: {sub_msg}")
            await ws.send(json.dumps(sub_msg))
            
            start = time.time()
            while time.time() - start < 30:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    print(f"Received: {msg[:200]}...")
                except asyncio.TimeoutError:
                    print("Timeout waiting for message...")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_ws())
