import json
import os

def analyze_paper_pnl(path="logs/paper_positions_v2.json"):
    if not os.path.exists(path):
        print(f"File {path} not found.")
        return

    with open(path, 'r') as f:
        data = json.load(f)

    positions = data.get("positions", [])
    realized_pnl = 0.0
    wins = 0
    losses = 0
    open_positions = 0
    unrealized_pnl = 0.0
    
    for pos in positions:
        state = pos.get("state", "OPEN")
        if state == "CLOSED":
            pnl = pos.get("pnl_usd", 0.0)
            realized_pnl += pnl
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
        else:
            open_positions += 1
            # Try to estimate unrealized pnl if current market price available
            current_price = pos.get("current_price") or pos.get("mark_price")
            if current_price is not None and pos.get("entry_price") is not None and pos.get("shares") is not None:
                entry_price = pos.get("entry_price")
                shares = pos.get("shares")
                unrealized_pnl += (current_price - entry_price) * shares
    
    print(f"Realized PnL: ${realized_pnl:.2f}")
    print(f"Wins: {wins}")
    print(f"Losses: {losses}")
    print(f"Win Rate: {wins/(wins+losses)*100:.1f}%" if (wins+losses) > 0 else "Win Rate: N/A")
    print(f"Open Positions: {open_positions}")
    print(f"Estimated Unrealized PnL: ${unrealized_pnl:.2f}")

    # Print brief per-position summary for visibility
    for pos in positions:
        print(f"- position_id={pos.get('position_id')} match={pos.get('match_id')} token={pos.get('token_id')} state={pos.get('state')} entry_price={pos.get('entry_price')} shares={pos.get('shares')} pnl={pos.get('pnl_usd')}")

if __name__ == "__main__":
    analyze_paper_pnl()
