"""Test the user's idea: after we back leader A, if A's net-worth lead SWINGS DOWN by a big
delta over ~5 min (momentum reversal) while A is STILL ahead, should the bot enter B (the
team gaining momentum)?

The clean question (controls for 'price leads the feed'): at the counter-swing moment, does
B win MORE than its level-implied fair (= momentum predicts beyond the lead level), and is
buying B at its then-current ask actually +EV? If B wins only at its level-fair rate, momentum
adds nothing. If A still usually wins, the swing reverted (Dota comeback) and B-buy is a loser.

Window = 5min (matches _lead_slope). Tests swing-drop thresholds 3000/4000/5000. Slip 0.02."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt
import winprob, yaml

SLIP, STAKE, WIN_NS = 0.02, 5.0, 300 * 1_000_000_000


def snap_winner_yes(side, rows):
    last = rows[-1]
    if (last["gt"] or 0) < 900 or abs(int(last["rl"])) < 2000:
        return None
    rw = 1 if int(last["rl"]) > 0 else 0
    return rw if side == "normal" else (1 - rw) if side == "reversed" else None


def value_entry(info, rows, book):
    """First value entry (back leader A). Returns (idx, row, tokA, sgnA) or None."""
    yt, nt = info["yes"], info["no"]
    for i, cur in enumerate(rows):
        gt, rl = cur["gt"], int(cur["rl"])
        if gt < 600 or abs(rl) < 3000:
            continue
        d = "radiant" if rl > 0 else "dire"
        side = ("YES" if d == "radiant" else "NO") if info["side"] == "normal" else \
               ("NO" if d == "radiant" else "YES") if info["side"] == "reversed" else None
        if side is None:
            continue
        tok = yt if side == "YES" else nt
        m0 = bt.book_mid_at(book, tok, cur["ns"])
        if m0 is None:
            continue
        ask = m0 + 0.005
        if ask > 0.84 or (abs(rl) > 5000 and ask < 0.35):
            continue
        if winprob.fair(abs(rl), gt, None) - ask < 0.10:
            continue
        return i, cur, tok, (1 if rl > 0 else -1)
    return None


def lead_at(rows, j, target_ns):
    """radiant_lead at the latest row <= target_ns, scanning back from index j."""
    for k in range(j, -1, -1):
        if rows[k]["ns"] <= target_ns:
            return int(rows[k]["rl"])
    return None


def main():
    m2info = bt.load_mapping()
    snaps = bt.load_snapshots()
    mk = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "markets.yaml"))["markets"]
    mtype = {str(m["dota_match_id"]): str(m.get("market_type")) for m in mk if str(m.get("dota_match_id") or "").isdigit()}
    joinable = [mid for mid in snaps if mid in m2info]
    tokens = set()
    for mid in joinable:
        tokens.add(m2info[mid]["yes"]); tokens.add(m2info[mid]["no"])
    book = bt.load_book_ticks(tokens)
    joined = [mid for mid in joinable if m2info[mid]["yes"] in book and m2info[mid]["no"] in book]

    # collect entries once
    entries = []
    for mid in joined:
        if mtype.get(mid, "") != "MAP_WINNER":
            continue
        info = m2info[mid]
        rows = [r for r in snaps[mid] if r["gt"] is not None and r["rl"] is not None]
        if len(rows) < 6:
            continue
        yw = bt.get_asset_winner(book, info["yes"])
        if yw is None:
            yw = snap_winner_yes(info["side"], rows)
        if yw is None:
            continue
        ent = value_entry(info, rows, book)
        if ent:
            entries.append((mid, info, rows, yw, ent))

    print(f"=== COUNTER-SWING TEST  (value entries: {len(entries)}) ===")
    print("  after backing A, A's lead drops >=DROP over 5min while A STILL leads -> consider buying B\n")

    for DROP in (3000, 4000, 5000):
        cs = []   # one counter-swing per match
        for mid, info, rows, yw, (i0, r0, tokA, sgnA) in entries:
            yt, nt = info["yes"], info["no"]
            tokB = nt if tokA == yt else yt
            for j in range(i0 + 1, len(rows)):
                cur = rows[j]
                leadA_now = int(cur["rl"]) * sgnA          # A's perspective
                if leadA_now <= 0:
                    break                                  # A no longer leads -> not "still leading"
                past = lead_at(rows, j, cur["ns"] - WIN_NS)
                if past is None:
                    continue
                leadA_past = past * sgnA
                if leadA_past - leadA_now < DROP:
                    continue                               # not a big enough down-swing
                mB = bt.book_mid_at(book, tokB, cur["ns"])
                if mB is None:
                    continue
                askB = mB + 0.005
                A_won = yw if tokA == yt else (1 - yw)
                B_won = 1 - A_won
                B_fair_level = 1.0 - winprob.fair(leadA_now, cur["gt"], None)  # level-implied P(B)
                cs.append(dict(askB=askB, Bfair=B_fair_level, Bwon=B_won, Awon=A_won))
                break
        n = len(cs)
        if n == 0:
            print(f"  DROP>={DROP}: 0 counter-swings"); continue
        bwon = sum(c["Bwon"] for c in cs) / n
        bfair = sum(c["Bfair"] for c in cs) / n
        bask = sum(c["askB"] for c in cs) / n
        roiB = sum(STAKE * (c["Bwon"] - min(c["askB"] + SLIP, 0.99)) / min(c["askB"] + SLIP, 0.99) for c in cs) / (STAKE * n)
        print(f"  DROP>={DROP}: n={n:>2} of {len(entries)} entries | B won {100*bwon:.0f}%  "
              f"B level-fair {100*bfair:.0f}%  B ask {100*bask:.0f}%  | buy-B ROI {100*roiB:+.0f}%")
        verdict = ("momentum PREDICTS (B won > level-fair)" if bwon - bfair > 0.08
                   else "A recovered / swing reverted" if bwon < 0.45
                   else "momentum adds ~nothing (B won ~ level-fair)")
        print(f"            -> A still won {100*(1-bwon):.0f}% (recovery rate).  {verdict}")
    print("\n  Read: buy-B ROI>0 AND B-won >> B level-fair => momentum has edge. Else the level+hold-to-settle stands.")


if __name__ == "__main__":
    main()
