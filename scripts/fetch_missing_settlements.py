import os
import json
import yaml
import pandas as pd
import asyncio
import aiohttp

def extract_winner_from_payload(p):
    prices_str = p.get("outcomePrices", "[]")
    if isinstance(prices_str, str):
        try:
            prices_str = json.loads(prices_str)
        except Exception:
            return None
    
    if isinstance(prices_str, list) and "1" in prices_str:
        idx = prices_str.index("1")
        outcomes = p.get("outcomes", "[]")
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except Exception:
                return None
        if isinstance(outcomes, list) and idx < len(outcomes):
            return outcomes[idx]
            
    return None

async def fetch_from_api(session, market_id):
    try:
        async with session.get(f"https://gamma-api.polymarket.com/markets/{market_id}", timeout=5) as r:
            if r.status != 200: return market_id, None
            m = await r.json()
            
        prices = m.get("outcomePrices")
        if prices:
            if isinstance(prices, str): prices = json.loads(prices)
            if "1" in prices:
                idx = prices.index("1")
                outcomes = m.get("outcomes")
                if isinstance(outcomes, str): outcomes = json.loads(outcomes)
                if isinstance(outcomes, list) and idx < len(outcomes):
                    return market_id, outcomes[idx]
                    
        slug = m.get("events", [{}])[0].get("slug") or m.get("slug")
        if slug:
            async with session.get(f"https://gamma-api.polymarket.com/events?slug={slug}", timeout=5) as e_r:
                if e_r.status == 200:
                    e_m = await e_r.json()
                    for ev in e_m:
                        for mk in ev.get("markets", []):
                            if str(mk.get("id")) == str(market_id):
                                tokens = mk.get("tokens", [])
                                for t in tokens:
                                    if t.get("winner") is True:
                                        return market_id, t.get("outcome")
    except Exception:
        pass
    return market_id, None

async def fetch_all(missing_ids):
    results = {}
    async with aiohttp.ClientSession() as session:
        tasks = []
        for mid in missing_ids:
            tasks.append(fetch_from_api(session, mid))
        
        # Concurrency limit
        semaphore = asyncio.Semaphore(10)
        async def sem_task(t):
            async with semaphore:
                return await t
                
        completed = 0
        total = len(tasks)
        for coro in asyncio.as_completed([sem_task(t) for t in tasks]):
            mid, winner = await coro
            if winner:
                results[mid] = winner
            completed += 1
            if completed % 50 == 0:
                print(f"Async API progress: {completed}/{total}")
    return results

def main():
    print("Loading markets...")
    markets_df = pd.DataFrame()
    with open("markets.yaml", "r") as f:
        y = yaml.safe_load(f)
        markets_df = pd.DataFrame(y["markets"])
        
    if os.path.exists("logs/runtime_markets.yaml"):
        with open("logs/runtime_markets.yaml", "r") as f:
            y_r = yaml.safe_load(f)
            if y_r and "markets" in y_r:
                r_df = pd.DataFrame(y_r["markets"])
                markets_df = pd.concat([markets_df, r_df], ignore_index=True)
                
    meta_path = "export_dataset/market_metadata.csv"
    if os.path.exists(meta_path):
        meta = pd.read_csv(meta_path)
    else:
        meta = pd.DataFrame(columns=["market_id", "resolved_outcome", "market_team_a_raw", "market_team_b_raw"])
        
    markets_df["market_id"] = markets_df["market_id"].astype(str)
    meta["market_id"] = meta["market_id"].astype(str)
    
    merged = pd.merge(markets_df, meta[["market_id", "resolved_outcome"]], on="market_id", how="left")
    missing_ids = merged[merged["resolved_outcome"].isna() & (merged["market_id"] != "POLY_MARKET_ID_HERE")]["market_id"].unique()
    
    print(f"Found {len(missing_ids)} missing market IDs.")
    
    missing_dict = {m: None for m in missing_ids}
    
    print("Parsing markets_raw.jsonl...")
    if os.path.exists("data/raw/polymarket/markets_raw.jsonl"):
        with open("data/raw/polymarket/markets_raw.jsonl", "r") as f:
            for line in f:
                if not missing_dict: break
                try:
                    data = json.loads(line)
                    p = data.get("payload", {})
                    mid = str(p.get("id"))
                    if mid in missing_dict and missing_dict[mid] is None:
                        winner = extract_winner_from_payload(p)
                        if winner:
                            missing_dict[mid] = winner
                except Exception:
                    pass
                    
    resolved_count = sum(1 for v in missing_dict.values() if v is not None)
    print(f"Found {resolved_count} out of {len(missing_ids)} in raw file.")
    
    still_missing = [mid for mid, winner in missing_dict.items() if winner is None]
    print(f"Falling back to API for the remaining {len(still_missing)}...")
    
    api_results = asyncio.run(fetch_all(still_missing))
    for mid, winner in api_results.items():
        missing_dict[mid] = winner
                
    resolved_count = sum(1 for v in missing_dict.values() if v is not None)
    print(f"Total resolved: {resolved_count}/{len(missing_ids)}")
    
    # Update meta
    new_rows = []
    for mid, winner in missing_dict.items():
        if winner:
            market_row = markets_df[markets_df["market_id"] == mid].iloc[0]
            new_rows.append({
                "market_id": mid,
                "resolved_outcome": winner,
                "market_team_a_raw": market_row["yes_team"],
                "market_team_b_raw": market_row["no_team"]
            })
            
    if new_rows:
        new_df = pd.DataFrame(new_rows)
        meta = meta[~meta["market_id"].isin(new_df["market_id"])]
        meta = pd.concat([meta, new_df], ignore_index=True)
        meta.to_csv(meta_path, index=False)
        print(f"Saved {len(new_rows)} new rows to {meta_path}")
    else:
        print("No new resolutions found.")

if __name__ == "__main__":
    main()
