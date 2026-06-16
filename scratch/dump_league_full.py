import asyncio
import aiohttp
import json
from steam_client import fetch_live_league_games

async def main():
    async with aiohttp.ClientSession() as session:
        games = await fetch_live_league_games(session)
        
    for g in games:
        if g.get("match_id") == 8825953969:
            print(json.dumps(g, indent=2))
            break
    else:
        if games:
            print(json.dumps(games[0], indent=2))

if __name__ == "__main__":
    asyncio.run(main())
