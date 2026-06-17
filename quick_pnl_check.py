import json
import os

def analyze_paper_pnl(path="logs/paper_positions_v2.json"):
    if not os.path.exists(path):
        print(f"File {path} not found.")
        return

    with open(path, 'r') as f:
        data = json.load(f)

    positions = data.get("positions", [])
    total_pnl = 0.0
    wins = 0
    losses = 0
    open_positions = 0
    
    for pos in positions:
        status = pos.get("status", "OPEN")
        if status == "CLOSED":
            pnl = pos.get("pnl_usd", 0.0)
            total_pnl += pnl
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
        else:
            open_positions += 1
            
    print(f"Total PnL: ${total_pnl:.2f}")
    print(f"Wins: {wins}")
    print(f"Losses: {losses}")
    print(f"Win Rate: {wins/(wins+losses)*100:.1f}%" if (wins+losses) > 0 else "Win Rate: N/A")
    print(f"Open Positions: {open_positions}")

if __name__ == "__main__":
    analyze_paper_pnl()
