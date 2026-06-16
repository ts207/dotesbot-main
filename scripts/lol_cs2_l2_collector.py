import asyncio
import json
import time
import yaml
import websockets
from pathlib import Path
from datetime import datetime, timezone

POLY_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
NEW_GAMES_YAML = Path("new_games_markets.yaml")
LOG_FILE = Path("logs/new_games_l2.jsonl")

def get_current_assets():
    if not NEW_GAMES_YAML.exists():
        return []
    try:
        with open(NEW_GAMES_YAML) as f:
            data = yaml.safe_load(f) or {"markets": []}
        assets = []
        for m in data.get("markets", []):
            tokens = m.get("token_ids")
            if isinstance(tokens, str):
                tokens = json.loads(tokens)
            if tokens and len(tokens) >= 2:
                assets.extend([str(tokens[0]), str(tokens[1])])
        return list(set(assets))
    except Exception as e:
        print(f"Error loading assets: {e}")
        return []

async def listen_l2():
    if not LOG_FILE.parent.exists():
        LOG_FILE.parent.mkdir(parents=True)

    subscribed_ids = set()

    while True:
        assets = get_current_assets()
        if not assets:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] No assets found in {NEW_GAMES_YAML}. Waiting...")
            await asyncio.sleep(60)
            continue

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Connecting to WS for {len(assets)} assets...")
        try:
            async with websockets.connect(POLY_WS_URL, ping_interval=20, ping_timeout=20) as ws:
                await ws.send(json.dumps({
                    "assets_ids": assets,
                    "type": "market"
                }))
                subscribed_ids = set(assets)
                print(f"Subscribed to {len(assets)} assets. Monitoring for activity...")
                
                while True:
                    try:
                        # Check for asset list changes every 30s
                        msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        data = json.loads(msg)
                        
                        log_entry = {
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "payload": data
                        }
                        
                        with open(LOG_FILE, "a") as f:
                            f.write(json.dumps(log_entry) + "\n")
                            
                    except asyncio.TimeoutError:
                        # Periodically check if we need to re-subscribe to new assets
                        current_assets = get_current_assets()
                        if set(current_assets) != subscribed_ids:
                            print("Asset list changed, reconnecting...")
                            break
                        # Heartbeat log to show we are still alive
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] WS Heartbeat (waiting for live activity)")
                        continue
                        
        except Exception as e:
            print(f"WS connection error: {e}. Reconnecting in 10s...")
            await asyncio.sleep(10)

if __name__ == "__main__":
    try:
        asyncio.run(listen_l2())
    except KeyboardInterrupt:
        print("Stopped.")
