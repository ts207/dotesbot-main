import json
import os
import csv
from pathlib import Path
from collections import defaultdict
import urllib.request

def load_markets_mapping(repo_root):
    import yaml
    try:
        with open(repo_root / "markets.yaml", "r") as f:
            data = yaml.safe_load(f)
            return {str(m["dota_match_id"]): m for m in data.get("markets", []) if m.get("dota_match_id")}
    except Exception:
        return {}

def fetch_radiant_win(match_id, cache):
    if match_id in cache:
        return cache[match_id]
    try:
        req = urllib.request.Request(f"https://api.opendota.com/api/matches/{match_id}",
                                     headers={"User-Agent": "curl/8"})
        d = json.load(urllib.request.urlopen(req, timeout=5))
        rw = d.get("radiant_win")
        cache[match_id] = rw
        return rw
    except Exception:
        return None

def compute_episode_pnls(episodes, repo_root):
    book_path = repo_root / "logs" / "book_events.csv"
    cache_path = repo_root / "logs" / "shadow_outcomes_cache.json"
    tok_map = load_markets_mapping(repo_root)
    
    cache = {}
    if cache_path.exists():
        with open(cache_path, "r") as f:
            try: cache = json.load(f)
            except: pass
            
    tokens_needed = set()
    for ep in episodes:
        if not ep: continue
        sig = ep[0]
        match_id = sig.get("match_id")
        side = sig.get("side")
        tok = sig.get("token_id")
        m = tok_map.get(str(match_id))
        if m and not tok:
            tok = m.get("yes_token_id") if side == "YES" else m.get("no_token_id")
            sig["token_id"] = str(tok)
        if tok:
            tokens_needed.add(str(tok))
            
    book_history = defaultdict(list)
    if book_path.exists() and tokens_needed:
        with open(book_path, "r") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) < 5: continue
                ts_iso = row[0]
                tok = row[1]
                if tok not in tokens_needed: continue
                try:
                    ask = float(row[4]) if row[4] else None
                    bid = float(row[3]) if row[3] else None
                except: continue
                if ask is None or bid is None: continue
                
                from datetime import datetime
                try:
                    ns = int(datetime.fromisoformat(ts_iso).timestamp() * 1e9)
                    book_history[tok].append((ns, bid, ask))
                except: pass
                
    results = []
    
    for ep in episodes:
        if not ep: continue
        
        entry_sig = ep[0]
        match_id = str(entry_sig.get("match_id"))
        side = entry_sig.get("side")
        tok = entry_sig.get("token_id")
        entry_ns = entry_sig.get("poll_ts") or entry_sig.get("ts_ns", 0)
        entry_ask = entry_sig.get("entry_ask")
        
        if not tok or entry_ask is None:
            results.append({"error": "missing_token_or_ask", "match_id": match_id, "side": side})
            continue
            
        m = tok_map.get(match_id)
        if not m:
            results.append({"error": "missing_mapping", "match_id": match_id, "side": side})
            continue
            
        rw = fetch_radiant_win(match_id, cache)
        
        mapping = m.get("steam_side_mapping", "normal").strip()
        is_yes = (side == "YES")
        our_is_radiant = (is_yes and mapping == "normal") or ((not is_yes) and mapping == "reversed")
        
        won = None
        if rw is not None:
            won = (rw and our_is_radiant) or ((not rw) and (not our_is_radiant))
            
        ep_result = {
            "match_id": match_id,
            "token_id": tok,
            "side": side,
            "entry_ns": entry_ns,
            "entry_ask": entry_ask,
            "settlement_pnl_available": rw is not None,
            "match_outcome_source": "opendota" if rw is not None else None,
            "market_settlement_source": "unavailable",
            "settlement_source_trusted": True if rw is not None else False,
            "markout_only": rw is None,
            "pnl_settle": None,
            "won": won
        }
        
        if won is not None:
            ep_result["pnl_settle"] = round((1.0 - entry_ask) if won else -entry_ask, 3)
            
        bh = book_history.get(tok, [])
        
        def get_bid_at(target_ns):
            best_bid = None
            for ns, bid, ask in bh:
                if ns <= target_ns:
                    best_bid = bid
                else:
                    break
            return best_bid
            
        bid_30s = get_bid_at(entry_ns + 30_000_000_000)
        bid_60s = get_bid_at(entry_ns + 60_000_000_000)
        bid_300s = get_bid_at(entry_ns + 300_000_000_000)
        bid_conv = bh[-1][1] if bh else None
            
        ep_result["pnl_30s"] = round(bid_30s - entry_ask, 3) if bid_30s is not None else None
        ep_result["pnl_60s"] = round(bid_60s - entry_ask, 3) if bid_60s is not None else None
        ep_result["pnl_300s"] = round(bid_300s - entry_ask, 3) if bid_300s is not None else None
        ep_result["pnl_to_convergence"] = round(bid_conv - entry_ask, 3) if bid_conv is not None else None
        
        results.append(ep_result)
        
    try:
        with open(cache_path, "w") as f:
            json.dump(cache, f)
    except: pass
        
    return results
