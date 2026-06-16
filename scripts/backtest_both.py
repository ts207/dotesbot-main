"""Backtest BOTH strategies on one data load:
  VALUE bot   = MAP_WINNER, back the leader (current deployed config: lead>=3000,
                edge>=0.10, price<=0.84), HOLD-TO-SETTLE (winner=$1).
  DSWING      = MATCH_WINNER (BO3 ML), decisive swing>=6000, exit at map-end.
Reports each strategy and the combined book (they trade different markets, so the
combined is additive). Winners from snapshot final lead (covers all matches)."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt
import winprob, yaml

N = 5.0
CHS = 0.005


def snap_winner_yes(info_side, rows):
    last = rows[-1]
    if (last["gt"] or 0) < 900 or abs(int(last["rl"])) < 2000:
        return None
    rw = 1 if int(last["rl"]) > 0 else 0
    return rw if info_side == "normal" else (1 - rw) if info_side == "reversed" else None


def main():
    t0 = time.time()
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
    print(f"matches with book: {len(joined)}  ({time.time()-t0:.0f}s)\n")

    value_tr, dswing_tr = [], []
    for mid in joined:
        info = m2info[mid]; yt, nt = info["yes"], info["no"]
        rows = [r for r in snaps[mid] if r["gt"] is not None and r["rl"] is not None]
        if len(rows) < 6:
            continue
        yw = bt.get_asset_winner(book, yt)
        if yw is None:
            yw = snap_winner_yes(info["side"], rows)
        if yw is None:
            continue
        mt = mtype.get(mid, "")
        # --- VALUE (MAP_WINNER) ---
        if mt == "MAP_WINNER":
            ey = en = False
            for cur in rows:
                if ey and en:
                    break
                gt, rl = cur["gt"], int(cur["rl"])
                if gt < 600 or abs(rl) < 3000:
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
                ask = m0 + CHS
                if ask > 0.84:
                    continue
                if abs(rl) > 5000 and ask < 0.35:
                    continue
                if winprob.fair(abs(rl), gt, None) - ask < 0.10:
                    continue
                won = yw if tok == yt else (1 - yw)
                value_tr.append(((1.0 if won else 0.0) - ask) / ask * N)
                if side == "YES":
                    ey = True
                else:
                    en = True
        # --- DSWING (MATCH_WINNER) ---
        elif mt == "MATCH_WINNER":
            m = srt[mid]
            entry = next((r for r in rows if (r["gt"] or 0) > 600 and abs(int(r["rl"])) >= 6000), None)
            if entry is None:
                continue
            wr = 1 if int(entry["rl"]) > 0 else 0
            if (1 if int(rows[-1]["rl"]) > 0 else 0) != wr:
                continue
            sm = str(m.get("steam_radiant_team") or "")
            yir = bool(sm and m.get("yes_team") and sm.lower() == str(m["yes_team"]).lower())
            wtok = (yt if yir else nt) if wr else (nt if yir else yt)
            endr = next((r for r in rows if r.get("go")), rows[-1])
            m_in = bt.book_mid_at(book, wtok, entry["ns"])
            m_out = bt.book_mid_at(book, wtok, endr["ns"] + 60 * 1_000_000_000)
            if m_in is None or m_out is None:
                continue
            e_in = min(m_in + CHS, 0.99); e_out = max(m_out - CHS, 0.01)
            if 0.02 < e_in < 0.98:
                dswing_tr.append((e_out - e_in) / e_in * N)

    def rep(label, tr):
        if not tr:
            print(f"  {label:26} n=0"); return
        n = len(tr); w = sum(1 for x in tr if x > 0); tot = sum(tr)
        print(f"  {label:26} n={n:>3}  win%={100*w/n:>4.0f}  $/trade={tot/n:+.3f}  total=${tot:+7.2f}  ROI={100*tot/(N*n):+5.1f}%")

    print("=== BOTH STRATEGIES ===")
    rep("VALUE (MAP, hold-settle)", value_tr)
    rep("DSWING (ML, map-end)", dswing_tr)
    rep("COMBINED", value_tr + dswing_tr)


if __name__ == "__main__":
    main()
