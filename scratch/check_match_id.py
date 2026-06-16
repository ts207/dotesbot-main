# What happened: the match_id field is None in markets.yaml for recent bound markets.
# The binder is storing the mapping but NOT writing back the steam match_id.
# Let's check the actual markets.yaml structure for one of these entries.

import yaml
with open('markets.yaml') as f:
    data = yaml.safe_load(f)
mlist = data if isinstance(data, list) else data.get('markets', [])

# Find a Jun 8-9 entry and show ALL its keys
recent = [m for m in mlist if '2026-06-08' in str(m.get('auto_mapped_at_utc',''))]
if recent:
    print("=== FULL YAML ENTRY FOR RECENT MATCH ===")
    for k, v in recent[0].items():
        print(f"  {k}: {v}")

print("\n=== KEY: MATCH_ID VALUES FOR RECENT MARKETS ===")
for m in recent:
    print(f"  {m.get('name','')[:60]} | match_id={repr(m.get('match_id'))} | steam_radiant={m.get('steam_radiant_team')} vs {m.get('steam_dire_team')}")
