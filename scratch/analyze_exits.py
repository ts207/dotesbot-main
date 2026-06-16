
import csv
import json
from pathlib import Path
from collections import defaultdict

def fnum(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None

def analyze_exits():
    positions_path = Path("logs/live_positions.json")
    exits_path = Path("logs/live_exits.csv")

    if not positions_path.exists():
        print("logs/live_positions.json not found")
        return

    with positions_path.open("r", encoding="utf-8") as f:
        pos_data = json.load(f)
    
    positions = {p["position_id"]: p for p in pos_data.get("positions", [])}

    if not exits_path.exists():
        print("logs/live_exits.csv not found")
        return

    exit_attempts = []
    with exits_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        exit_attempts = list(reader)

    print(f"Total exit attempts: {len(exit_attempts)}")
    
    stats_by_reason = defaultdict(lambda: {"count": 0, "filled_shares": 0, "total_shares": 0, "pnl": 0.0, "attempts": 0})
    
    unique_positions_exited = set()

    for att in exit_attempts:
        pos_id = att.get("position_id")
        reason = att.get("reason")
        filled = fnum(att.get("shares_filled")) or 0.0
        requested = fnum(att.get("shares_requested")) or 0.0
        bid = fnum(att.get("best_bid"))
        
        pos = positions.get(pos_id)
        if not pos:
            continue

        stats_by_reason[reason]["attempts"] += 1
        stats_by_reason[reason]["filled_shares"] += filled
        
        if pos_id not in unique_positions_exited:
            stats_by_reason[reason]["count"] += 1
            stats_by_reason[reason]["total_shares"] += pos["shares"]
            
            # Theoretical PnL if this was the final bid
            if bid is not None:
                entry_price = pos["entry_price"]
                pnl = (bid - entry_price) * pos["shares"]
                stats_by_reason[reason]["pnl"] += pnl
            
            unique_positions_exited.add(pos_id)

    print("\n=== Exit Analysis by Reason ===")
    print(f"{'Reason':<20} | {'Pos Count':<5} | {'Attempts':<8} | {'Fill %':<8} | {'Theo PnL':<10}")
    print("-" * 65)
    for reason, s in stats_by_reason.items():
        fill_pct = (s["filled_shares"] / s["total_shares"] * 100) if s["total_shares"] > 0 else 0
        print(f"{reason:<20} | {s['count']:<9} | {s['attempts']:<8} | {fill_pct:>7.1f}% | {s['pnl']:>+10.2f}")

    print("\n=== Position Details ===")
    for pid, pos in positions.items():
        print(f"Pos: {pos['market_name'][:40]}...")
        print(f"  Event: {pos['event_type']} | Side: {pos['side']} | Entry: {pos['entry_price']:.2f}")
        print(f"  State: {pos['state']} | Reason: {pos.get('exit_reason')} | Attempts: {pos.get('exit_attempts')}")
        
        # Find last attempt bid
        last_bid = None
        for att in reversed(exit_attempts):
            if att["position_id"] == pid:
                last_bid = fnum(att.get("best_bid"))
                break
        
        if last_bid is not None:
            theo_pnl = (last_bid - pos['entry_price']) * pos['shares']
            print(f"  Last Bid: {last_bid:.2f} | Theo PnL: {theo_pnl:>+6.2f}")
        else:
            print("  No exit attempts recorded.")

if __name__ == "__main__":
    analyze_exits()
