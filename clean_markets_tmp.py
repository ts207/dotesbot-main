import sys
import re
import os

with open("markets.yaml", "r") as f:
    content = f.read()

blocks = content.split("- name:")
new_blocks = [blocks[0]]

for block in blocks[1:]:
    if "auto_mapped_at_utc" in block or "steam_side_mapping" in block:
        # Scrub it
        block = re.sub(r"\n\s+auto_mapped_at_utc:.*\n", "\n", block)
        block = re.sub(r"\n\s+auto_mapped_source:.*\n", "\n", block)
        block = re.sub(r"\n\s+steam_radiant_team:.*\n", "\n", block)
        block = re.sub(r"\n\s+steam_dire_team:.*\n", "\n", block)
        block = re.sub(r"\n\s+steam_side_mapping:.*\n", "\n", block)
        block = re.sub(r"\n\s+confidence: 1\.0\n", "\n  confidence: 0.0\n", block)
        block = re.sub(r"\n\s+dota_match_id: ['\"]?[0-9]+['\"]?\n", "\n  dota_match_id: STEAM_MATCH_OR_LOBBY_ID_HERE\n", block)
        
    new_blocks.append(block)

with open("markets.yaml", "w") as f:
    f.write("- name:".join(new_blocks))

print("Cleaned markets.yaml")
