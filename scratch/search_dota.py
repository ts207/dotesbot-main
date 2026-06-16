import asyncio
import aiohttp
import json

async def main():
    headers = {"Accept-Encoding": "gzip, deflate, identity"}
    async with aiohttp.ClientSession(headers=headers) as session:
        params = {"active": "true", "closed": "false", "limit": 1000}
        async with session.get("https://gamma-api.polymarket.com/markets", params=params) as r:
            markets = await r.json()
            
    dota_markets = []
    for m in markets:
        text = str(m.get("question", "")).lower() + " " + str(m.get("title", "")).lower()
        if "dota" in text:
            dota_markets.append({
                "question": m.get("question"),
                "outcomes": m.get("outcomes"),
                "clobTokenIds": m.get("clobTokenIds")
            })
            
    print(json.dumps(dota_markets, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
