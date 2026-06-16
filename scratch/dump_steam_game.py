import asyncio
import aiohttp
import json
from steam_client import fetch_top_live_games

async def main():
    async with aiohttp.ClientSession() as session:
        games = await fetch_top_live_games(session)
        
    if games:
        print(json.dumps(games[0], indent=2))
    else:
        print("No games found.")

if __name__ == "__main__":
    asyncio.run(main())
