import urllib.request, json
try:
    data = json.loads(urllib.request.urlopen('https://api.opendota.com/api/proMatches').read().decode())
    for m in data:
        s = str(m).lower()
        if 'nande' in s or '4iki' in s:
            print(m)
except Exception as e:
    print(e)
