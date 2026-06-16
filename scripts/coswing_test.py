"""Test the user's refined teamfight signal: a real teamfight = kills AND net-worth swinging
TOGETHER toward the opponent (not raw kill-diff, not gold alone). Question: after we back A,
when the lead swings toward B, does requiring KILLS to confirm the swing (co-swing = teamfight)
predict A losing better than a gold-only swing (which reverts, A recovered 67%)?

If A's win-rate after a gold+kill CO-SWING is much lower than after a gold-only swing, the
co-swing is the real cut/hedge trigger. Window 90s. Ground-truth outcomes."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt
import winprob, yaml

WIN_NS = 90 * 1_000_000_000
NW_SWING = 2000          # net-worth moved toward B by >= this over 90s
KILL_SWING = 2           # kills moved toward B by >= this over 90s
OUT = {str(k): bool(v) for k, v in json.load(open(Path(__file__).resolve().parent.parent / "logs" / "opendota_outcomes.json")).items()}


def snap_winner_yes(side, rows):
    last = rows[-1]
    if (last["gt"] or 0) < 900 or abs(int(last["rl"])) < 2000:
        return None
    rw = 1 if int(last["rl"]) > 0 else 0
    return rw if side == "normal" else (1 - rw) if side == "reversed" else None


def gt_yes(mid, side):
    rw = OUT.get(str(mid))
    if rw is None:
        return None
    rw = 1 if rw else 0
    return rw if side == "normal" else (1 - rw) if side == "reversed" else None


def back(rows, i, target_ns, key):
    for k in range(i, -1, -1):
        if rows[k]["ns"] <= target_ns:
            return rows[k][key]
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

    # buckets of A-outcome after the first adverse swing of each type
    groups = {"gold_only": [], "coswing": [], "no_swing": []}
    for mid in joined:
        if mtype.get(mid, "") != "MAP_WINNER":
            continue
        info = m2info[mid]; yt, nt = info["yes"], info["no"]
        rows = [r for r in snaps[mid] if r["gt"] is not None and r["rl"] is not None]
        if len(rows) < 6:
            continue
        yw = gt_yes(mid, info["side"])
        if yw is None:
            yw = bt.get_asset_winner(book, yt) or snap_winner_yes(info["side"], rows)
        if yw is None:
            continue
        # value entry on A
        ent = None
        for i, cur in enumerate(rows):
            gt, rl = cur["gt"], int(cur["rl"])
            if gt < 600 or abs(rl) < 3000:
                continue
            tok = yt if ((rl > 0) == (info["side"] == "normal")) else nt
            m0 = bt.book_mid_at(book, tok, cur["ns"])
            if m0 is None or m0 + 0.005 > 0.84:
                continue
            if winprob.fair(abs(rl), gt, None) - (m0 + 0.005) < 0.10:
                continue
            ent = (i, cur, tok, 1 if rl > 0 else -1); break
        if ent is None:
            continue
        i0, r0, tokA, sgnA = ent
        A_won = yw if tokA == yt else (1 - yw)
        # scan forward for first adverse swing; classify gold-only vs co-swing
        kind = "no_swing"
        for j in range(i0 + 1, len(rows)):
            cur = rows[j]
            nw_now = int(cur["rl"]) * sgnA
            nw_past = back(rows, j, cur["ns"] - WIN_NS, "rl")
            if nw_past is None:
                continue
            nw_drop = (nw_past * sgnA) - nw_now           # >0 = lead moved toward B
            if nw_drop < NW_SWING:
                continue
            # kills: A's kill-lead now vs 90s ago
            k_now = (cur["rs"] - cur["ds"]) * sgnA
            rs_p, ds_p = back(rows, j, cur["ns"] - WIN_NS, "rs"), back(rows, j, cur["ns"] - WIN_NS, "ds")
            if rs_p is None or ds_p is None:
                continue
            k_drop = ((rs_p - ds_p) * sgnA) - k_now       # >0 = kills moved toward B
            kind = "coswing" if k_drop >= KILL_SWING else "gold_only"
            break
        groups[kind].append(A_won)

    print(f"=== CO-SWING (kills+gold) vs GOLD-ONLY swing — does A (backed) still win? ===")
    print(f"  window {WIN_NS//10**9}s | swing thresholds: net-worth>={NW_SWING}, kills>={KILL_SWING}\n")
    for k in ("no_swing", "gold_only", "coswing"):
        g = groups[k]
        if not g:
            print(f"  {k:10}: n=0"); continue
        wr = sum(g) / len(g)
        tag = ""
        if k == "coswing":
            tag = "  <-- if A wins MUCH less here than gold_only, co-swing is the real cut/hedge trigger"
        print(f"  {k:10}: n={len(g):>2}  A still won {100*wr:>4.0f}%  (A lost {100*(1-wr):.0f}%){tag}")
    print("\n  Read: gold_only A-win ~67% (reverts→hold). If coswing A-win << that, kills confirm the teamfight is real.")


if __name__ == "__main__":
    main()
