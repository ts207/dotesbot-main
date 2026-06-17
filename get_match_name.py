import sqlite3
import json

conn = sqlite3.connect('logs/state_v2.sqlite')
cursor = conn.cursor()
try:
    cursor.execute("SELECT radiant_name, dire_name FROM matches WHERE match_id='8855025901'")
    res = cursor.fetchone()
    if res:
        print(f"Match 8855025901: {res[0]} vs {res[1]}")
    else:
        print("Match not found in matches table.")
except Exception as e:
    print(e)
conn.close()
