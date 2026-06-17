import sqlite3
import time

conn = sqlite3.connect('logs/state_v2.sqlite')
cursor = conn.cursor()

try:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print("Tables:", tables)

    # If there's a live_games or live_markets table
    if ('live_games',) in tables:
        cursor.execute("SELECT match_id, radiant_name, dire_name FROM live_games")
        print("Live Games:", cursor.fetchall())
        
    if ('active_markets',) in tables:
        cursor.execute("SELECT match_id, market_name FROM active_markets")
        print("Active Markets:", cursor.fetchall())
        
    if ('markets',) in tables:
        cursor.execute("SELECT title FROM markets WHERE active=1")
        print("Markets:", cursor.fetchall())
except Exception as e:
    print("Error:", e)

conn.close()
