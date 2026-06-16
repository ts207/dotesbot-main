import yaml
m = yaml.safe_load(open('markets.yaml'))
for k,v in m.items():
    if not isinstance(v, dict): continue
    if k in ['unmapped_series', 'live_matches_cache']: continue
    if not v.get('resolved') and not v.get('ended'):
        print(f'Match: {k} - Polymarket: {v.get("market_name", "Unknown")} - Type: {v.get("market_type", "Unknown")}')
