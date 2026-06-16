"""Extract the FULL candidate-trade dataset ONCE (the slow part = loading 153M snapshots + book),
so all edge-mining afterward is instant. For every MAP_WINNER match with ground truth + book, at
every ~45s while a team leads, record the LEADER's-token features + the realized hold-to-settle
return. Then mine_trades.py slices it any way without reloading.

Columns: match_id, gt, lead, kill_lead, slope5, fair, ask, edge, won, ret  -> logs/trade_dataset.csv"""
import csv, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt
import winprob

STEP_NS = 45 * 1_000_000_000
SLOPE_NS = 300 * 1_000_000_000
OUT = {str(k): bool(v) for k, v in json.load(open(Path(__file__).resolve().parent.parent / "logs" / "opendota_outcomes.json")).items()}


def back(rows, j, target_ns, key):
    for k in range(j, -1, -1):
        if rows[k]["ns"] <= target_ns:
            return rows[k][key]
    return None


def main():
    import yaml
    mk = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "markets.yaml"))["markets"]
    # MAP_WINNER-only mapping, keyed by match_id — DON'T collapse with MATCH_WINNER markets
    # (the old load_mapping last-wins overwrite silently dropped ~40% of MAP_WINNER matches).
    m2info = {}
    for m in mk:
        mid = str(m.get("dota_match_id") or "")
        if not (mid.isdigit() and mid != "123") or str(m.get("market_type")) != "MAP_WINNER":
            continue
        m2info[mid] = {"yes": m["yes_token_id"], "no": m["no_token_id"],
                       "side": m.get("steam_side_mapping", "normal")}
    snaps = bt.load_snapshots()
    tokens = set()
    for mid in snaps:
        if mid in m2info:
            tokens.add(m2info[mid]["yes"]); tokens.add(m2info[mid]["no"])
    book = bt.load_book_ticks(tokens)
    joined = [mid for mid in snaps if mid in m2info and m2info[mid]["yes"] in book and m2info[mid]["no"] in book]

    out_rows = []
    for mid in joined:
        info = m2info[mid]; yt, nt = info["yes"], info["no"]; side = info["side"]
        if side not in ("normal", "reversed"):
            continue
        rw = OUT.get(str(mid))
        if rw is None:
            continue
        yes_won = (1 if rw else 0) if side == "normal" else (0 if rw else 1)
        rows = [r for r in snaps[mid] if r["gt"] is not None and r["rl"] is not None
                and r["rs"] is not None and r["ds"] is not None]
        if len(rows) < 6:
            continue
        last_ns = 0
        for j, cur in enumerate(rows):
            gt, rl = cur["gt"], int(cur["rl"])
            if gt < 600 or abs(rl) < 500 or cur["ns"] - last_ns < STEP_NS:
                continue
            sgn = 1 if rl > 0 else -1
            tok = yt if ((sgn > 0) == (side == "normal")) else nt
            m0 = bt.book_mid_at(book, tok, cur["ns"])
            if m0 is None:
                continue
            ask = round(m0 + 0.005, 4)
            if ask <= 0.02 or ask >= 0.99:
                continue
            fair = round(winprob.fair(abs(rl), gt, None), 4)
            kill_lead = (cur["rs"] - cur["ds"]) * sgn
            # GetTopLive deltas (leader perspective): 5-min gold slope, 60s gold & kill swings
            rlp = back(rows, j, cur["ns"] - SLOPE_NS, "rl")
            slope5 = (rl - int(rlp)) * sgn if rlp is not None else 0
            rl60 = back(rows, j, cur["ns"] - 60_000_000_000, "rl")
            gold_sw60 = (rl - int(rl60)) * sgn if rl60 is not None else 0
            rs60, ds60 = back(rows, j, cur["ns"] - 60_000_000_000, "rs"), back(rows, j, cur["ns"] - 60_000_000_000, "ds")
            kill_sw60 = ((cur["rs"] - cur["ds"]) - (rs60 - ds60)) * sgn if rs60 is not None else 0
            won = yes_won if tok == yt else (1 - yes_won)
            ret = round((won - ask) / ask, 4)
            out_rows.append([mid, gt, abs(rl), kill_lead, slope5, gold_sw60, kill_sw60,
                             fair, ask, round(fair - ask, 4), won, ret])
            last_ns = cur["ns"]

    path = Path(__file__).resolve().parent.parent / "logs" / "trade_dataset.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["match_id", "gt", "lead", "kill_lead", "slope5", "gold_sw60", "kill_sw60", "fair", "ask", "edge", "won", "ret"])
        w.writerows(out_rows)
    print(f"wrote {len(out_rows)} candidate rows from {len(set(r[0] for r in out_rows))} matches -> {path}")


if __name__ == "__main__":
    main()
