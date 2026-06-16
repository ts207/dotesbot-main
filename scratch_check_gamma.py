import json

with open('logs/polymarket_discovery_fetches.jsonl', 'r') as f:
    for line in f:
        if 'kibamboni' in line.lower() or 'nande' in line.lower():
            try:
                data = json.loads(line)
                for e in data:
                    s = str(e).lower()
                    if 'kibamboni' in s or 'nande' in s:
                        print(e.get('title'))
                        for m in e.get('markets', []):
                            print('  Market:', m.get('question'), 'Outcomes:', m.get('outcomes'))
            except:
                pass
