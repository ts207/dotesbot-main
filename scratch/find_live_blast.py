import asyncio
import aiohttp
import json
from steam_client import fetch_all_live_games

async def main():
    async with aiohttp.ClientSession() as session:
        games = await fetch_all_live_games(session)
        
    print(f"Found {len(games)} live games total")
    
    blast_teams = {
        "Tundra Esports", "Aurora", "BetBoom Team", "Team Falcons", 
        "GLYPH", "Team Liquid", "PARIVISION", "Xtreme Gaming", 
        "OG", "ex-HEROIC", "Team Spirit", "Team Yandex"
    }
    
    found_blast = []
    for g in games:
        r = str(g.get("radiant_team", ""))
        d = str(g.get("dire_team", ""))
        lid = str(g.get("league_id", ""))
        
        if any(team.lower() in r.lower() or team.lower() in d.lower() for team in blast_teams) or lid == "19350":
            found_blast.append(g)
            
    for g in found_blast:
        print(f"Match {g['match_id']} (League {g['league_id']}): {g['radiant_team']} vs {g['dire_team']}")
        print(f"  Time: {g['game_time_sec']}s")
        print("-" * 20)

if __name__ == "__main__":
    asyncio.run(main())
