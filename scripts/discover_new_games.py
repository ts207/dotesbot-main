import aiohttp
import json
import sys
import os
import re
import yaml
import time
from pathlib import Path
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from poly_gamma import fetch_active_markets

NEW_GAMES_YAML = ROOT / "new_games_markets.yaml"
POLYMARKET_ORIGIN = "https://polymarket.com"

# Category URLs
LOL_GAMES_URL = "https://polymarket.com/esports/league-of-legends/games"
CS2_GAMES_URL = "https://polymarket.com/esports/cs2/games"

def _extract_next_data(html: str) -> dict:
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, flags=re.S)
    if not match:
        return {}
    try:
        return json.loads(unescape(match.group(1)))
    except json.JSONDecodeError:
        return {}

def _walk_markets(obj) -> list[dict]:
    out: list[dict] = []
    if isinstance(obj, dict):
        if "clobTokenIds" in obj and ("question" in obj or "title" in obj):
            out.append(obj)
        for value in obj.values():
            out.extend(_walk_markets(value))
    elif isinstance(obj, list):
        for value in obj:
            out.extend(_walk_markets(value))
    return out

async def fetch_page_markets(session: aiohttp.ClientSession, url: str) -> list[dict]:
    headers = {"user-agent": "Mozilla/5.0", "Accept-Encoding": "identity"}
    try:
        async with session.get(url, timeout=15, headers=headers) as r:
            if r.status != 200:
                print(f"Error fetching {url}: {r.status}")
                return []
            html = await r.text()
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return []

    data = _extract_next_data(html)
    return _walk_markets(data)

def load_existing():
    if not NEW_GAMES_YAML.exists():
        return {"markets": []}
    with open(NEW_GAMES_YAML) as f:
        return yaml.safe_load(f) or {"markets": []}

def save_new(markets, game_label):
    existing = load_existing()
    existing_ids = {str(m.get("market_id")) for m in existing["markets"]}
    
    new_count = 0
    for m in markets:
        mid = str(m.get("id") or m.get("marketId"))
        name = m.get("question") or m.get("title")
        if "handicap" in str(name).lower():
            continue
            
        if mid not in existing_ids:
            entry = {
                "name": name,
                "market_id": mid,
                "token_ids": m.get("clobTokenIds"),
                "outcomes": m.get("outcomes"),
                "slug": m.get("slug"),
                "game": game_label,
                "first_seen_at": datetime.now(timezone.utc).isoformat(),
            }
            existing["markets"].append(entry)
            new_count += 1
            
    with open(NEW_GAMES_YAML, "w") as f:
        yaml.dump(existing, f, sort_keys=False)
    return new_count

async def run_discovery():
    async with aiohttp.ClientSession() as session:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Polling LoL markets...")
        lol_markets = await fetch_page_markets(session, LOL_GAMES_URL)
        lol_count = save_new(lol_markets, "LoL")
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Polling CS2 markets...")
        cs2_markets = await fetch_page_markets(session, CS2_GAMES_URL)
        cs2_count = save_new(cs2_markets, "CS2")
        
        print(f"Total new markets: LoL={lol_count}, CS2={cs2_count}")

if __name__ == "__main__":
    import asyncio
    print("Starting LoL/CS2 market discovery service...")
    try:
        while True:
            asyncio.run(run_discovery())
            time.sleep(300) # Poll every 5 minutes
    except KeyboardInterrupt:
        print("Stopped.")
