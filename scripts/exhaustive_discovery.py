import asyncio
import aiohttp
import json

async def main():
    headers = {"user-agent": "Mozilla/5.0", "Accept-Encoding": "identity"}
    async with aiohttp.ClientSession(headers=headers) as session:
        offset = 0
        limit = 100
        found = []
        while offset < 5000:
            params = {
                "active": "true",
                "closed": "false",
                "limit": limit,
                "offset": offset
            }
            async with session.get("https://gamma-api.polymarket.com/markets", params=params) as r:
                if r.status != 200:
                    print(f"Error {r.status} at offset {offset}")
                    break
                markets = await r.json()
                if not markets:
                    break
                
                for m in markets:
                    text = " ".join(str(m.get(k, "") or "") for k in ["question", "title", "slug"])
                    if "handicap" in text.lower():
                        continue
                        
                    if any(k in text.lower() for k in ["lol", "league", "cs2", "cs:go", "counter-strike", "esport", "gen.g"]):
                        tokens_raw = m.get("clobTokenIds")
                        if isinstance(tokens_raw, str):
                            try:
                                tokens = json.loads(tokens_raw)
                            except:
                                tokens = []
                        else:
                            tokens = tokens_raw or []
                            
                        if len(tokens) >= 2:
                            found.append({
                                "question": m.get("question"),
                                "id": m.get("id"),
                                "yes_token": tokens[0],
                                "no_token": tokens[1]
                            })
                
                offset += limit
                
        print(f"Found {len(found)} esports markets.")
        for m in found:
            print(f"  - {m.get('question')} | ID: {m.get('id')} | YES: {m.get('yes_token')} | NO: {m.get('no_token')}")

if __name__ == "__main__":
    asyncio.run(main())
