import asyncio
import aiohttp
import json

async def main():
    # Explicitly request identity/gzip to avoid 'br' if local aiohttp/brotli is broken
    headers = {"Accept-Encoding": "gzip, deflate, identity"}
    async with aiohttp.ClientSession(headers=headers) as session:
        params = {"active": "true", "closed": "false", "limit": 1000}
        async with session.get("https://gamma-api.polymarket.com/markets", params=params) as r:
            markets = await r.json()
            
    blast_markets = []
    for m in markets:
        text = str(m.get("question", "")).lower() + " " + str(m.get("title", "")).lower()
        if "blast" in text:
            blast_markets.append(m)
            
    print(json.dumps(blast_markets, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
