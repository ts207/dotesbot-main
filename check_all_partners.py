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
        params = {"key": STEAM_API_KEY, "partner": 1}
        async with session.get(url_top, params=params) as r:
            data = await r.json()
            games = data.get("game_list", [])
            print(f"Total games in Partner 1: {len(games)}")
            for g in games:
                if "8854136736" == str(g.get("match_id")):
                    print(f"FOUND IT: {g}")
                    return
            print("Not found in the list.")

asyncio.run(check())
