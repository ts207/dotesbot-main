"""Test the user's idea: when the net-worth lead REVERSES after the first entry (the OTHER
team takes a 3000+ lead), should the bot FLIP — exit the original side and back the new
leader — or HOLD to settle?

This is the empirical question that decides it: Dota is the comeback game, so a 'reversal'
often re-reverses (the flip sells the bottom). For every value entry on side A, find the
first qualifying reversal (lead crosses to -3000), and compare:
  HOLD : keep A to settlement.
  FLIP : sell A at the reversal-time bid, buy B (new leader) at the reversal-time ask, hold B.
Reports reversal frequency, the re-reversal (comeback-back) rate, and HOLD vs FLIP P&L.
Slip 0.02, $5 stakes, winners from book final / snapshot final lead."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.backtest_value_engine as bt
import winprob, yaml

SLIP, STAKE, MIN_LEAD, MIN_EDGE = 0.02, 5.0, 3000, 0.10


def snap_winner_yes(side, rows):
    last = rows[-1]
    if (last["gt"] or 0) < 900 or abs(int(last["rl"])) < 2000:
        return None
    rw = 1 if int(last["rl"]) > 0 else 0
    return rw if side == "normal" else (1 - rw) if side == "reversed" else None


def qualifies(info, rl, gt, book, ns):
    """Return (token, ask, dir) if a value entry qualifies at this row, else None."""
    if gt < 600 or abs(rl) < MIN_LEAD:
        return None
    d = "radiant" if rl > 0 else "dire"
    side = ("YES" if d == "radiant" else "NO") if info["side"] == "normal" else \
           ("NO" if d == "radiant" else "YES") if info["side"] == "reversed" else None
    if side is None:
        return None
    tok = info["yes"] if side == "YES" else info["no"]
    m0 = bt.book_mid_at(book, tok, ns)
    if m0 is None:
        return None
    ask = m0 + 0.005
    if ask > 0.84 or (abs(rl) > 5000 and ask < 0.35):
        return None
    if winprob.fair(abs(rl), gt, None) - ask < MIN_EDGE:
        return None
    return tok, ask, side


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

    n_entries = 0
    revs = []   # per reversal: dict
    for mid in joined:
        if mtype.get(mid, "") != "MAP_WINNER":
            continue
        info = m2info[mid]; yt, nt = info["yes"], info["no"]
        rows = [r for r in snaps[mid] if r["gt"] is not None and r["rl"] is not None]
        if len(rows) < 6:
            continue
        yw = bt.get_asset_winner(book, yt)
        if yw is None:
            yw = snap_winner_yes(info["side"], rows)
        if yw is None:
            continue
        # first entry (either side)
        ent = None
        for i, cur in enumerate(rows):
            q = qualifies(info, int(cur["rl"]), cur["gt"], book, cur["ns"])
            if q:
                ent = (i, cur, q); break
        if ent is None:
            continue
        n_entries += 1
        i0, r0, (tokA, askA, sideA) = ent
        sgnA = 1 if int(r0["rl"]) > 0 else -1
        # first reversal AFTER entry: lead crosses to >=3000 the OTHER way and B qualifies
        for cur in rows[i0 + 1:]:
            rl = int(cur["rl"])
            if (1 if rl > 0 else -1) == sgnA or abs(rl) < MIN_LEAD:
                continue
            qB = qualifies(info, rl, cur["gt"], book, cur["ns"])
            if not qB:
                continue
            tokB, askB, sideB = qB
            bidA = bt.book_mid_at(book, tokA, cur["ns"])
            if bidA is None:
                continue
            sellA = max(bidA - 0.005, 0.01)
            A_won = yw if tokA == yt else (1 - yw)
            B_won = 1 - A_won
            hold_pnl = STAKE * (A_won - askA) / askA
            flipA = STAKE * (sellA - askA) / askA               # realized loss selling A
            flipB = STAKE * (B_won - askB) / askB
            revs.append(dict(mid=mid, A_won=A_won, askA=askA, sellA=sellA, askB=askB,
                             hold=hold_pnl, flip=flipA + flipB, gtrev=cur["gt"]))
            break

    print(f"=== REVERSAL TEST  (entries={n_entries}, qualifying reversals={len(revs)}) ===\n")
    if not revs:
        print("no qualifying reversals — the lead rarely crosses to -3000 after a 3000+ entry."); return
    nr = len(revs)
    a_won = sum(r["A_won"] for r in revs)
    print(f"  reversal frequency: {nr}/{n_entries} = {100*nr/n_entries:.0f}% of entries saw the lead cross to -3000")
    print(f"  re-reversal (comeback-back): original side A still WON {a_won}/{nr} = {100*a_won/nr:.0f}% of reversals")
    print(f"     -> when A still wins, FLIP sold the bottom AND backed the loser (double wrong)\n")
    hold = sum(r["hold"] for r in revs); flip = sum(r["flip"] for r in revs)
    print(f"  on these {nr} matches, total P&L (${STAKE:.0f} stakes):")
    print(f"     HOLD (keep A to settle): ${hold:+.2f}   ({100*hold/(STAKE*nr):+.1f}% ROI)")
    print(f"     FLIP (sell A, back B):   ${flip:+.2f}   ({100*flip/(STAKE*nr):+.1f}% ROI)")
    print(f"     -> {'FLIP wins' if flip > hold else 'HOLD wins'} by ${abs(flip-hold):.2f}")
    print(f"  avg sell price for A at reversal: {sum(r['sellA'] for r in revs)/nr:.2f}"
          f"  (you realize this much of your ${STAKE:.0f} entry when flipping)")


if __name__ == "__main__":
    main()
