from mapping import load_valid_mappings
m, _ = load_valid_mappings()
print(f"Total mappings: {len(m)}")
print([x.get('dota_match_id') for x in m if str(x.get('dota_match_id')) in ['8845570042', '8845538960', '8845544460']])
