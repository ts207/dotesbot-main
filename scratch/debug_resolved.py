import asyncio
import json
from auto_series_binder import fetch_polymarket_dota_events

def main():
    events = fetch_polymarket_dota_events()
    print(f"Total Events: {len(events)}")
    for e in events:
        if e.get("active") == False or e.get("closed") == True:
            print(f"RESOLVED OR CLOSED: {e.get('title')} | Active: {e.get('active')} | Closed: {e.get('closed')}")
        else:
            # Check if any markets inside it are resolved
            for m in e.get("markets", []):
                if m.get("closed") == True or m.get("active") == False:
                     print(f"RESOLVED MARKET: {m.get('question')} | Active: {m.get('active')} | Closed: {m.get('closed')}")

if __name__ == "__main__":
    main()
