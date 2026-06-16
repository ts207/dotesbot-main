import subprocess
import json
import re
import html
import sys

def fetch_page(url):
    cmd = ["curl", "-s", "-H", "user-agent: Mozilla/5.0", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout

def extract_next_data(page_html):
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page_html, flags=re.S)
    if not match: return {}
    try: return json.loads(html.unescape(match.group(1)))
    except: return {}

def walk_markets(obj):
    out = []
    if isinstance(obj, dict):
        if "clobTokenIds" in obj and ("question" in obj or "title" in obj):
            out.append(obj)
        for value in obj.values():
            out.extend(walk_markets(value))
    elif isinstance(obj, list):
        for value in obj:
            out.extend(walk_markets(value))
    return out

def main():
    listing_url = "https://polymarket.com/esports/dota-2/games"
    listing_html = fetch_page(listing_url)
    
    hrefs = re.findall(r'href="([^"]*?/esports/dota-2/[^"]+)"', listing_html)
    urls = set()
    for h in hrefs:
        h = html.unescape(h)
        if "/dota2-" not in h: continue
        if h.startswith("/"): h = "https://polymarket.com" + h
        urls.add(h)
        
    print(f"Found {len(urls)} event URLs")
    
    all_markets = []
    for url in urls:
        event_html = fetch_page(url)
        data = extract_next_data(event_html)
        markets = walk_markets(data)
        for m in markets:
            m["source_url"] = url
            all_markets.append(m)
            
    unique = {}
    for m in all_markets:
        cid = m.get("conditionId") or m.get("id")
        if cid: unique[cid] = m
        
    results = []
    for m in unique.values():
        q = m.get("question") or m.get("title")
        if not q: continue
        
        # Recognize BLAST Slam Match Winner (BO1 or BO3)
        is_blast = "BLAST Slam" in q
        is_winner = "Winner" in q or "vs" in q
        
        if not (is_blast and is_winner): continue
        
        tokens = m.get("clobTokenIds")
        if isinstance(tokens, str): tokens = json.loads(tokens)
        if not tokens or len(tokens) < 2: continue
        
        outcomes = m.get("outcomes")
        if isinstance(outcomes, str): outcomes = json.loads(outcomes)
        
        # Determine market type
        mtype = "MATCH_WINNER"
        if "Game" in q and "Winner" in q:
            mtype = "MAP_WINNER"
            
        results.append({
            "name": q,
            "yes_token_id": tokens[0],
            "no_token_id": tokens[1],
            "yes_team": outcomes[0] if outcomes else "Team A",
            "no_team": outcomes[1] if outcomes else "Team B",
            "market_type": mtype,
            "source_url": m.get("source_url")
        })
        
    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    main()
