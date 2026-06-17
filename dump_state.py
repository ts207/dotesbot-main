import sqlite3
import os

def dump_sqlite(path="logs/state_v2.sqlite"):
    if not os.path.exists(path):
        print(f"File {path} not found.")
        return

    conn = sqlite3.connect(path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print(f"Tables: {tables}")
    
    for table_name in [t[0] for t in tables]:
        print(f"\n--- {table_name} ---")
        try:
            cursor.execute(f"SELECT * FROM {table_name} LIMIT 5;")
            rows = cursor.fetchall()
            for row in rows:
                print(row)
        except Exception as e:
            print(f"Error reading {table_name}: {e}")
    
    conn.close()

if __name__ == "__main__":
    dump_sqlite()
