import asyncio
import aiohttp
import json
from steam_client import fetch_all_live_games

async def main():
    async with aiohttp.ClientSession() as session:
        games = await fetch_all_live_games(session)
        
    for g in games:
        if g['match_id'] == '8825915270':
            print(json.dumps(g, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
