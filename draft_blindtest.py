"""Blind draft backtest — validate whether my draft reads beat a coinflip.

Protocol (no peeking):
  1. --fetch N : pull N recent PRO matches, cache full data (WITH outcomes) to a
     hidden file, then display ONLY the drafts (team A heroes vs team B heroes,
     numbered). No winner, no score, no net-worth shown.
  2. I read the drafts and commit a call for each (A or B) — reasoning from the
     heroes alone, meta-informed via hero_meta.json.
  3. --reveal "A,B,A,..." : score my ordered calls against the cached outcomes.

This is the honest test: blind calls on real drafts, scored on a real sample.
"""
from __future__ import annotations
import json, os, sys, time, urllib.request

DATA = "logs/blindtest_data.json"
HEROES = "logs/hero_id_map.json"
META = "logs/hero_meta.json"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    return json.load(urllib.request.urlopen(req, timeout=25))


def hero_map():
    if os.path.exists(HEROES):
        return {int(k): v for k, v in json.load(open(HEROES)).items()}
    hs = _get("https://api.opendota.com/api/heroes")
    m = {h["id"]: h["localized_name"] for h in hs}
    json.dump(m, open(HEROES, "w"))
    return m


def fetch(n):
    hm = hero_map()
    meta = json.load(open(META)) if os.path.exists(META) else {}
    print(f"  pulling recent pro matches...", flush=True)
    pro = _get("https://api.opendota.com/api/proMatches")[: n * 3]  # over-fetch, some lack draft
    games = []
    for pm in pro:
        if len(games) >= n:
            break
        mid = pm.get("match_id")
        try:
            d = _get(f"https://api.opendota.com/api/matches/{mid}")
            time.sleep(1.0)
        except Exception:
            continue
        players = d.get("players") or []
        rad = [hm.get(p.get("hero_id"), p.get("hero_id")) for p in players if p.get("isRadiant")]
        dire = [hm.get(p.get("hero_id"), p.get("hero_id")) for p in players if not p.get("isRadiant")]
        if len(rad) != 5 or len(dire) != 5 or d.get("radiant_win") is None:
            continue
        games.append({
            "match_id": mid,
            "A_team": pm.get("radiant_name") or "Radiant", "A": rad,
            "B_team": pm.get("dire_name") or "Dire", "B": dire,
            "A_won": bool(d.get("radiant_win")),
            "league": pm.get("league_name", ""),
        })
        print(f"    got {len(games)}/{n}", flush=True)
    json.dump(games, open(DATA, "w"))
    # display drafts ONLY
    def metawr(h):
        m = meta.get(h, {})
        w = m.get("pro_wr") or m.get("pub_wr")
        return f"{w*100:.0f}%" if w else "?"
    print(f"\n=== BLIND DRAFTS ({len(games)} games) — call A or B for each, drafts only ===")
    for i, g in enumerate(games, 1):
        print(f"\n  GAME {i}:")
        print(f"    A = {g['A_team']:<16} {', '.join(f'{h}({metawr(h)})' for h in g['A'])}")
        print(f"    B = {g['B_team']:<16} {', '.join(f'{h}({metawr(h)})' for h in g['B'])}")
    print(f"\n  → make your blind calls, then: python3 draft_blindtest.py --reveal \"A,B,...\"")


def reveal(calls_str):
    if not os.path.exists(DATA):
        print("  no fetched games — run --fetch first"); return
    games = json.load(open(DATA))
    calls = [c.strip().upper() for c in calls_str.split(",") if c.strip()]
    if len(calls) != len(games):
        print(f"  WARNING: {len(calls)} calls for {len(games)} games");
    n = min(len(calls), len(games))
    correct = 0
    print(f"\n=== REVEAL ({n} games) ===")
    for i in range(n):
        g = games[i]; call = calls[i]
        won_team = "A" if g["A_won"] else "B"
        hit = (call == won_team)
        correct += hit
        winner = g["A_team"] if g["A_won"] else g["B_team"]
        print(f"  GAME {i+1}: called {call} ({g[call+'_team']}) | WON: {won_team} ({winner})  {'✓' if hit else '✗'}")
    print(f"\n  SCORE: {correct}/{n} = {correct/n*100:.0f}%")
    print(f"  baseline (coinflip): 50%")
    if n >= 10:
        edge = "✓ BEATS coinflip — draft read has signal" if correct/n > 0.6 else ("≈ coinflip — no real edge" if correct/n < 0.6 else "marginal")
        print(f"  VERDICT: {edge}")
    else:
        print(f"  (need 10+ games for a real read; this is {n})")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        print(__doc__)
    elif a[0] == "--fetch":
        fetch(int(a[1]) if len(a) > 1 else 12)
    elif a[0] == "--reveal" and len(a) > 1:
        reveal(a[1])
    else:
        print(__doc__)
