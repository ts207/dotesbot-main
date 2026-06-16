import asyncio
import aiohttp
import re

async def main():
    url = "https://polymarket.com/esports/dota-2/games"
    headers = {"user-agent": "Mozilla/5.0"}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url) as r:
            html = await r.text()
            
    print(f"HTML length: {len(html)}")
    hrefs = re.findall(r'href="([^"]*?/esports/dota-2/[^"]+)"', html)
    print(f"Found {len(hrefs)} esports links")
    for h in hrefs:
        print(f"  {h}")

if __name__ == "__main__":
    asyncio.run(main())
