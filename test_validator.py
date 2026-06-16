import yaml
from mapping_validator import validate_mapping_identity
from team_utils import norm_team

with open("markets.yaml") as f:
    md = yaml.safe_load(f)

# Find our mapping
mapping = [m for m in md["markets"] if "2552697" in str(m.get("market_id"))][0]

# Construct a simulated game state representing the live game master vs grey track game
game = {
    "radiant_team": "Grey Track",
    "dire_team": "Game Master",
    "radiant_team_id": "10157562",
    "dire_team_id": "10008067",
    "league_id": "19893",
    "match_id": "8853931510",
}

print("Mapping:")
print(f"  yes_team: {mapping.get('yes_team')}")
print(f"  no_team: {mapping.get('no_team')}")
print(f"  steam_radiant_team: {mapping.get('steam_radiant_team')}")
print(f"  steam_dire_team: {mapping.get('steam_dire_team')}")

print("\nGame:")
print(f"  radiant_team: {game.get('radiant_team')}")
print(f"  dire_team: {game.get('dire_team')}")

res = validate_mapping_identity(mapping, game)
print(f"\nvalidate_mapping_identity result:")
print(f"  ok: {res.ok}")
print(f"  mapping_errors: {res.mapping_errors}")
print(f"  mapping_confidence: {res.mapping_confidence}")
