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
        for p in range(15):
            params = {"key": STEAM_API_KEY, "partner": p}
            try:
                async with session.get(url_top, params=params) as r:
                    data = await r.json()
                    games = data.get("game_list", [])
                    print(f"Partner {p}: {len(games)} games")
                    for g in games:
                        if "Ivory" in str(g.get("team_name_radiant", "")) or "Ivory" in str(g.get("team_name_dire", "")):
                            print(f"  FOUND Ivory in partner {p}: {g.get('match_id')}")
            except:
                pass

asyncio.run(check())
