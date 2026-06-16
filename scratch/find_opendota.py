import requests
import time
from datetime import datetime

def find_recent_matches():
    url = "https://api.opendota.com/api/proMatches"
    resp = requests.get(url)
    if resp.status_code != 200:
        print("Failed to fetch")
        return
    matches = resp.json()
    for m in matches:
        rad_name = (m.get("radiant_name") or "").lower()
        dire_name = (m.get("dire_name") or "").lower()
        if "zero" in rad_name or "zero" in dire_name or "summer" in rad_name or "summer" in dire_name or "balu" in rad_name or "balu" in dire_name:
            st = datetime.fromtimestamp(m['start_time'])
            print(f"Found Match: {m['match_id']} | {rad_name} vs {dire_name} | Start: {st}")

if __name__ == "__main__":
    find_recent_matches()
