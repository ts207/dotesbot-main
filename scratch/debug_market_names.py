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
    
    url = list(urls)[0]
    print(f"Checking {url}")
    event_html = fetch_page(url)
    data = extract_next_data(event_html)
    markets = walk_markets(data)
    for m in markets[:10]:
        print(f"Market: {m.get('question') or m.get('title')}")
        print(f"Outcomes: {m.get('outcomes')}")
        print(f"Tokens: {m.get('clobTokenIds')}")
        print("-" * 20)

if __name__ == "__main__":
    main()
