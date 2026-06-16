import asyncio
import os
import json
from live_executor import LiveCLOBClient
from dotenv import load_dotenv

load_dotenv()

async def check_trades():
    client = LiveCLOBClient()
    # get_trades(limit=10)
    resp = await asyncio.to_thread(client._client.get_trades)
    print(json.dumps(resp, indent=2))

if __name__ == "__main__":
    asyncio.run(check_trades())
