import asyncio
from auto_series_binder import fetch_polymarket_dota_events, fetch_steam_games, extract_live_game_market, extract_series_market
import json

async def main():
    events = fetch_polymarket_dota_events()
    steam_games = await fetch_steam_games()
    valid_live_games = [g for g in steam_games if (g.get("game_time") or g.get("game_time_sec") or 0) > 0]
    
    print(f"--- POLYMARKET EVENTS ({len(events)}) ---")
    for e in events:
        print(f"Event: {e.get('title')}")
        m = extract_live_game_market(e)
        if m: print(f"  Live Mkt: {m.get('question')} | Outcomes: {m.get('outcomes')}")
        s = extract_series_market(e)
        if s: print(f"  Ser Mkt: {s.get('question')} | Outcomes: {s.get('outcomes')}")

    print(f"\n--- STEAM LIVE GAMES ({len(valid_live_games)}) ---")
    for g in valid_live_games:
        print(f"Match: {g.get('match_id')} | {g.get('radiant_team')} vs {g.get('dire_team')} | GT: {g.get('game_time') or g.get('game_time_sec')}")

if __name__ == "__main__":
    asyncio.run(main())
