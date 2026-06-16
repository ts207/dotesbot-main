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
        for p in [0, 1, 2, 3]:
            params = {"key": STEAM_API_KEY, "partner": p}
            async with session.get(url_top, params=params) as r:
                data = await r.json()
                games = data.get("game_list", [])
                for g in games:
                    if "8854136736" == str(g.get("match_id")):
                        print(f"FOUND 8854136736 in partner {p}")
                        return
    print("Not found in any Top Live partner")

asyncio.run(check())
