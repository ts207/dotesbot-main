import asyncio
import aiohttp
import json
import yaml
import time
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys_path = str(ROOT)
import sys
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

# Markets to track (IDs found via discovery)
TRACK_MARKETS = [
    {"name": "LoL: ZennIT vs The Bandits - Game 1 Winner", "id": "1259818"},
    {"name": "LoL: ZennIT vs The Bandits - Game 2 Winner", "id": "1259819"},
    {"name": "LoL: ZennIT vs The Bandits (BO3)", "id": "1259817"},
]

NEW_GAMES_LOG = ROOT / "logs" / "new_games_snapshots.csv"

async def fetch_market_prices(session: aiohttp.ClientSession, market_id: str):
    url = f"https://gamma-api.polymarket.com/markets/{market_id}"
    try:
        async with session.get(url, timeout=10) as r:
            if r.status == 200:
                return await r.json()
    except Exception as e:
        print(f"Error fetching {market_id}: {e}")
    return None

async def main():
    if not NEW_GAMES_LOG.parent.exists():
        NEW_GAMES_LOG.parent.mkdir(parents=True)
    
    if not NEW_GAMES_LOG.exists():
        with open(NEW_GAMES_LOG, "w") as f:
            f.write("received_at_utc,market_id,name,best_bid,best_ask,mid\n")

    async with aiohttp.ClientSession(headers={"user-agent": "Mozilla/5.0", "Accept-Encoding": "identity"}) as session:
        print(f"Starting collection for {len(TRACK_MARKETS)} markets...")
        while True:
            for m in TRACK_MARKETS:
                print(f"Fetching {m['name']}...")
                data = await fetch_market_prices(session, m["id"])
                if data:
                    print(f"Got data for {m['name']}")
                    bid = data.get("bestBid", "0")
                    ask = data.get("bestAsk", "1")
                    # Some markets might not have clob data in this endpoint
                    # Gamma sometimes returns prices directly
                    
                    row = [
                        datetime.now(timezone.utc).isoformat(),
                        m["id"],
                        m["name"],
                        str(bid),
                        str(ask),
                        str((float(bid) + float(ask)) / 2 if bid and ask else "")
                    ]
                    
                    with open(NEW_GAMES_LOG, "a") as f:
                        f.write(",".join(row) + "\n")
            
            await asyncio.sleep(10) # Poll every 10 seconds for research

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped.")
