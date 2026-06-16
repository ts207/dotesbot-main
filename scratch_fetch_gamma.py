import requests
r = requests.get('https://gamma-api.polymarket.com/events?tag_slug=dota-2&closed=false&limit=200')
for e in r.json():
    print(e.get('title'))
