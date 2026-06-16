"""Value sweep with GROUND-TRUTH winner labels (logs/opendota_outcomes.json = match_id->radiant_win),
instead of the net-worth-sign fallback (which assumes the leader won = circular, inflates win%).
Reports how many matches were labeled by ground truth vs proxy, and the honest win/ROI/CI."""
import json, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt
import winprob, yaml

random.seed(7)
SLIP = 0.02
OUT = {str(k): bool(v) for k, v in json.load(open(Path(__file__).resolve().parent.parent / "logs" / "opendota_outcomes.json")).items()}


def snap_winner_yes(side, rows):
    last = rows[-1]
    if (last["gt"] or 0) < 900 or abs(int(last["rl"])) < 2000:
        return None
    rw = 1 if int(last["rl"]) > 0 else 0
    return rw if side == "normal" else (1 - rw) if side == "reversed" else None


def yes_won_groundtruth(mid, side):
    rw = OUT.get(str(mid))
    if rw is None:
        return None
    rw = 1 if rw else 0
    return rw if side == "normal" else (1 - rw) if side == "reversed" else None


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

    src = {"groundtruth": 0, "book": 0, "networth": 0}
    disagree = 0  # ground truth vs net-worth proxy disagree (the mislabels)
    M = []
    for mid in joined:
        if mtype.get(mid, "") != "MAP_WINNER":
            continue
        info = m2info[mid]
        rows = [r for r in snaps[mid] if r["gt"] is not None and r["rl"] is not None]
        if len(rows) < 6:
            continue
        gt_w = yes_won_groundtruth(mid, info["side"])
        bk_w = bt.get_asset_winner(book, info["yes"])
        nw_w = snap_winner_yes(info["side"], rows)
        if gt_w is not None:
            yw = gt_w; src["groundtruth"] += 1
            if nw_w is not None and nw_w != gt_w:
                disagree += 1
        elif bk_w is not None:
            yw = bk_w; src["book"] += 1
        elif nw_w is not None:
            yw = nw_w; src["networth"] += 1
        else:
            continue
        M.append((mid, info, rows, yw))

    def select(min_lead, min_fair, min_edge):
        trades = []
        for mid, info, rows, yw in M:
            yt, nt = info["yes"], info["no"]
            ey = en = False
            for cur in rows:
                if ey and en:
                    break
                gt, rl = cur["gt"], int(cur["rl"])
                if gt < 600 or abs(rl) < min_lead:
                    continue
                d = "radiant" if rl > 0 else "dire"
                side = ("YES" if d == "radiant" else "NO") if info["side"] == "normal" else \
                       ("NO" if d == "radiant" else "YES") if info["side"] == "reversed" else None
                if side is None or (side == "YES" and ey) or (side == "NO" and en):
                    continue
                tok = yt if side == "YES" else nt
                m0 = bt.book_mid_at(book, tok, cur["ns"])
                if m0 is None:
                    continue
                ask = m0 + 0.005
                if ask > 0.84 or (abs(rl) > 5000 and ask < 0.35):
                    continue
                if winprob.fair(abs(rl), gt, None) < min_fair or winprob.fair(abs(rl), gt, None) - ask < min_edge:
                    continue
                won = yw if tok == yt else (1 - yw)
                trades.append((m0, won))
                if side == "YES":
                    ey = True
                else:
                    en = True
        return trades

    def metrics(tr):
        n = len(tr)
        if n == 0:
            return None
        rets = [(w - (m + SLIP)) / (m + SLIP) for m, w in tr]
        wr = sum(w for _, w in tr) / n
        bs = sorted(sum(rets[random.randrange(n)] for _ in range(n)) / n for _ in range(10000))
        return n, wr, sum(rets) / n, bs[250], bs[9750], sum(1 for b in bs if b > 0) / 10000

    print(f"=== VALUE SWEEP — GROUND-TRUTH LABELS  (matches: {len(M)}) ===")
    print(f"  winner source: ground-truth={src['groundtruth']}  book={src['book']}  net-worth-proxy={src['networth']}")
    print(f"  net-worth proxy DISAGREED with ground truth on {disagree}/{src['groundtruth']} labeled matches"
          f"  ({100*disagree/max(src['groundtruth'],1):.0f}% would have been MISLABELED by the old method)\n")
    print(f"  {'lead':>5} {'minfair':>7} {'minedge':>7} | {'n':>3} {'win%':>5} {'ROI':>6} {'P(>0)':>6} {'95% CI':>18}")
    for lead in (0, 1000, 2000, 3000):
        for mf in (0.0, 0.70):
            for me in (0.10, 0.15):
                r = metrics(select(lead, mf, me))
                if not r:
                    continue
                n, wr, roi, lo, hi, pos = r
                print(f"  {lead:>5} {mf:>7.2f} {me:>7.2f} | {n:>3} {100*wr:>4.0f}% {100*roi:>+5.1f}% {pos:>6.2f}  [{100*lo:>+5.1f}%,{100*hi:>+5.1f}%]")


if __name__ == "__main__":
    main()
