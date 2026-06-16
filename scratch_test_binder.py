import asyncio
from auto_series_binder import find_live_steam_match, fetch_steam_games
async def main():
    sg = await fetch_steam_games()
    vlg = [g for g in sg if (g.get('game_time') or g.get('game_time_sec') or 0) > 0]
    match = find_live_steam_match('4ikibamboni', 'Nande+4', vlg)
    print('Match:', match.get('match_id') if match else None)
asyncio.run(main())
