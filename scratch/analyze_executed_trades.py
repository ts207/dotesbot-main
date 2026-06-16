import asyncio
import os
import json
from datetime import datetime, timezone
from live_executor import LiveCLOBClient
from dotenv import load_dotenv

load_dotenv()

async def analyze_trades():
    client = LiveCLOBClient()
    trades = await asyncio.to_thread(client._client.get_trades)
    
    print(f"{'Time (UTC)':<20} | {'Side':<4} | {'Asset':<10} | {'Size':<8} | {'Price':<6} | {'Outcome':<15}")
    print("-" * 80)
    
    for t in trades:
        dt = datetime.fromtimestamp(int(t['match_time']), tz=timezone.utc)
        time_str = dt.strftime('%Y-%m-%d %H:%M:%S')
        asset_short = t['asset_id'][:10] + "..."
        print(f"{time_str:<20} | {t['side']:<4} | {asset_short:<10} | {t['size']:<8} | {t['price']:<6} | {t['outcome']:<15}")

if __name__ == "__main__":
    asyncio.run(analyze_trades())
