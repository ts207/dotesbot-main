import requests
import json

def main():
    url = "https://gamma-api.polymarket.com/events?slug=dota2-gamema-greytr-2026-06-16"
    r = requests.get(url)
    print(f"Status Code: {r.status_code}")
    if r.status_code == 200:
        print(json.dumps(r.json(), indent=2))
    else:
        print(r.text)

if __name__ == '__main__':
    main()
