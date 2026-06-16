import asyncio
import aiohttp
import json
from steam_client import fetch_live_league_games

async def main():
    async with aiohttp.ClientSession() as session:
        games = await fetch_live_league_games(session)
        
    for g in games:
        sb = g.get("scoreboard", {})
        if sb:
            # Print players from radiant and dire
            rad = sb.get("radiant", {}).get("players", [])
            dire = sb.get("dire", {}).get("players", [])
            if rad or dire:
                print(f"Match {g.get('match_id')}: Found {len(rad)} radiant and {len(dire)} dire players")
                print(json.dumps(rad[0] if rad else dire[0], indent=2))
                break
    else:
        print("No games with players found.")

if __name__ == "__main__":
    asyncio.run(main())
