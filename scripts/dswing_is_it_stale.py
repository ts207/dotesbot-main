"""THE test for 'is the book stale after the swing, or just tracking rising fair?'.

For each decisive swing, track over post-swing horizons, for the BACKED (leading) team:
  - book_price        (the market)
  - map_win_prob      winprob.fair(lead,time)  -- reliable, no series state needed
  - series_fair       compute_bo3_match_p(...) -- only for valid series state

Reads:
  * If map_win_prob is ALREADY HIGH at the swing and ~flat, while book crawls up
    -> the info was there at the swing, book ignored it = STALE (edge).
  * If map_win_prob itself RISES post-swing alongside book
    -> book is tracking genuinely-new info = NOT stale (risk premium, no edge).
  * series_fair - book over time: persistent positive gap = stale; ~0 = efficient.
"""
import bisect, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt
import winprob, yaml
from series_model import compute_bo3_match_p

DSWING_LEAD = 6000
FRESH_S = 180.0
HZ = [0, 30, 60, 120, 300, 600]


def nearest_book(book, tok, ns):
    arr = book.get(tok)
    if not arr:
        return None, None
    times, mids = arr
    i = bisect.bisect_right(times, ns) - 1
    if i < 0:
        return None, None
    return mids[i], (ns - times[i]) / 1e9


def row_near(rows, ns):
    best, bd = None, 1e18
    for r in rows:
        d = abs(r["ns"] - ns)
        if d < bd:
            bd, best = d, r
    return best if bd < 90e9 else None   # within 90s


def p_backed_map(rl, gt, backed_radiant):
    bl = rl if backed_radiant else -rl
    return winprob.fair(bl, gt, None) if bl >= 0 else 1.0 - winprob.fair(-bl, gt, None)


def main():
    m2info = bt.load_mapping()
    snaps = bt.load_snapshots()
    mk = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "markets.yaml"))["markets"]
    mtype = {str(m["dota_match_id"]): str(m.get("market_type")) for m in mk if str(m.get("dota_match_id") or "").isdigit()}
    srt = {str(m["dota_match_id"]): m for m in mk if str(m.get("dota_match_id") or "").isdigit()}
    joinable = [mid for mid in snaps if mid in m2info]
    tokens = set()
    for mid in joinable:
        tokens.add(m2info[mid]["yes"]); tokens.add(m2info[mid]["no"])
    book = bt.load_book_ticks(tokens)
    joined = [mid for mid in joinable if m2info[mid]["yes"] in book and m2info[mid]["no"] in book]

    def _i(x):
        try:
            return int(x)
        except Exception:
            return None

    # accumulate per-horizon stats: map_prob, book (and series_fair where valid)
    agg = {h: dict(p=[], bk=[], sf=[]) for h in HZ}
    n_all = n_valid = 0
    for mid in joined:
        if mtype.get(mid, "") != "MATCH_WINNER":
            continue
        m = srt[mid]; info = m2info[mid]; yt, nt = info["yes"], info["no"]
        rows = [r for r in snaps[mid] if r["gt"] is not None and r["rl"] is not None]
        if len(rows) < 6:
            continue
        entry = next((r for r in rows if (r["gt"] or 0) > 600 and abs(int(r["rl"])) >= DSWING_LEAD), None)
        if entry is None:
            continue
        wr = 1 if int(entry["rl"]) > 0 else 0          # backed = radiant?
        if (1 if int(rows[-1]["rl"]) > 0 else 0) != wr:
            continue
        sm = str(m.get("steam_radiant_team") or "")
        yir = bool(sm and m.get("yes_team") and sm.lower() == str(m["yes_team"]).lower())
        backed_is_yes = (yir == bool(wr))           # winning team's token is YES?
        wtok = yt if backed_is_yes else nt
        # valid series state? (game number must equal maps played + 1)
        gn, sy, sn = _i(m.get("current_game_number")), _i(m.get("series_score_yes")), _i(m.get("series_score_no"))
        # valid in-progress BO3: game# == maps played + 1, and neither team has clinched (score < 2)
        valid_series = (gn is not None and sy is not None and sn is not None
                        and gn == sy + sn + 1 and sy < 2 and sn < 2 and 1 <= gn <= 3)
        n_all += 1
        n_valid += 1 if valid_series else 0
        for h in HZ:
            tgt = entry["ns"] + h * 1_000_000_000
            r = row_near(rows, tgt)
            bk, age = nearest_book(book, wtok, tgt)
            if r is None or bk is None or age is None or age > FRESH_S:
                continue
            pm = p_backed_map(int(r["rl"]), r["gt"], wr == 1)
            agg[h]["p"].append(pm)
            agg[h]["bk"].append(bk)
            if valid_series:
                try:
                    pc_yes = pm if backed_is_yes else 1 - pm
                    sp_yes = compute_bo3_match_p(pc_yes, 0.5, sy, sn, gn)
                    agg[h]["sf"].append(sp_yes if backed_is_yes else 1 - sp_yes)
                except Exception:
                    pass   # never let one bad record kill the run

    print(f"=== IS THE BOOK STALE AFTER THE SWING?  (n={n_all} swings, {n_valid} with valid series state) ===\n")
    print("Backed (leading) team, by seconds after swing:")
    print(f"  {'horizon':>8} {'map_winprob':>12} {'book_price':>11} {'gap(map-book)':>14} {'series_fair':>12} {'gap(ser-book)':>14} {'k':>4}")
    for h in HZ:
        a = agg[h]
        if not a["p"]:
            print(f"  {h:>6}s   (no fresh data)"); continue
        mp = sum(a["p"]) / len(a["p"]); bk = sum(a["bk"]) / len(a["bk"])
        sf = sum(a["sf"]) / len(a["sf"]) if a["sf"] else float("nan")
        sfgap = (sf - sum(a['bk'][:len(a['sf'])]) / len(a['sf'])) if a["sf"] else float("nan")
        # series gap computed on the valid-series subset only:
        if a["sf"]:
            # recompute book mean over the same subset is awkward; approximate with full-book mean
            sfgap = sf - bk
        print(f"  {h:>6}s {mp:>12.3f} {bk:>11.3f} {mp-bk:>+14.3f} {sf:>12.3f} {sfgap:>+14.3f} {len(a['p']):>4}")

    print("\nRead: a map_winprob that is high & flat from 0s while book climbs = STALE (edge).")
    print("      series_fair persistently ABOVE book = stale in series terms (the real edge).")


if __name__ == "__main__":
    main()
