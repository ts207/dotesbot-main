import os
import asyncio
import aiohttp
import json
from dotenv import load_dotenv

load_dotenv()
GAMMA_URL = "https://gamma-api.polymarket.com/events?active=true&closed=false&limit=100"

async def check():
    async with aiohttp.ClientSession() as session:
        async with session.get(GAMMA_URL) as r:
            data = await r.json()
            dota_events = [e for e in data if "Dota" in e.get("title", "") or any("Dota" in m.get("group_id", "") for m in e.get("markets", []))]
            print(f"Found {len(dota_events)} Dota events on Polymarket")
            for e in dota_events:
                title = e.get("title", "")
                if "4ikibamboni" in title or "Spirit Academy" in title:
                    print(f"MATCH FOUND: {title}")
                    for m in e.get("markets", []):
                        print(f"  Market: {m.get('question')} (ID: {m.get('id')})")

asyncio.run(check())
