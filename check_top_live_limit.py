import os
import asyncio
import aiohttp
import json
from dotenv import load_dotenv

load_dotenv()
STEAM_API_KEY = os.getenv("STEAM_API_KEY")

async def check():
    url_top = "https://api.steampowered.com/IDOTA2Match_570/GetTopLiveGame/v1/"
    async with aiohttp.ClientSession() as session:
        # Try with a larger num_games request
        params = {"key": STEAM_API_KEY, "partner": 1, "num_games": 100}
        async with session.get(url_top, params=params) as r:
            data = await r.json()
            games = data.get("game_list", [])
            print(f"Partner 1 (requested 100): {len(games)} games")
            for g in games:
                if "Ivory" in str(g.get("team_name_radiant", "")) or "Ivory" in str(g.get("team_name_dire", "")):
                    print(f"  FOUND Ivory: {g.get('match_id')}")
            
            # Print first 3 games just to see
            for i, g in enumerate(games[:3]):
                print(f"  {i}: [{g.get('match_id')}] {g.get('team_name_radiant')} vs {g.get('team_name_dire')}")

asyncio.run(check())
