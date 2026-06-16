"""Run LoL market discovery once and print what's there. No WS, no logging."""
from __future__ import annotations
import asyncio, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'scripts'))

import aiohttp
from poly_gamma import parse_clob_token_ids
from lol_book_collector import (
    POLYMARKET_LOL_PAGE, _extract_lol_event_urls, _fetch_event_markets,
)

async def main():
    headers = {"user-agent": "Mozilla/5.0", "Accept-Encoding": "gzip, deflate"}
    async with aiohttp.ClientSession() as session:
        async with session.get(POLYMARKET_LOL_PAGE, timeout=15, headers=headers) as r:
            r.raise_for_status()
            html = await r.text()
        urls = _extract_lol_event_urls(html)
        print(f"Found {len(urls)} lol-* event URLs on listing page\n")
        for u in urls[:10]:
            print(f"  {u}")
        if len(urls) > 10:
            print(f"  ... ({len(urls)-10} more)")
        print()
        results = await asyncio.gather(*(_fetch_event_markets(session, u) for u in urls))
    markets = [m for sub in results for m in sub]
    print(f"Fetched {len(markets)} match-winner markets across all events\n")
    with_tokens = [(m, parse_clob_token_ids(m)) for m in markets]
    with_tokens = [(m, ty, tn) for m, (ty, tn) in with_tokens if ty and tn]
    print(f"{len(with_tokens)} have CLOB token IDs\n")
    for m, ty, tn in with_tokens[:20]:
        q = (m.get('question') or '')[:80]
        end = (m.get('endDate') or '')[:10]
        print(f"  {q}  end={end}  yes={ty[:14]}...")

asyncio.run(main())
