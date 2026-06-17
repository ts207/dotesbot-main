import sqlite3
import os
import json

def analyze_sqlite_pnl(path="logs/state_v2.sqlite"):
    if not os.path.exists(path):
        print(f"File {path} not found.")
        return

    conn = sqlite3.connect(path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT pnl_usd, mode, raw_json FROM closed_positions;")
        rows = cursor.fetchall()
        
        total_pnl = 0.0
        wins = 0
        losses = 0
        strategy_stats = {}
        
        for pnl, mode, raw_json in rows:
            total_pnl += pnl
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
            
            # Try to extract strategy from raw_json
            strategy = "unknown"
            try:
                data = json.loads(raw_json)
                strategy = data.get("strategy_kind") or data.get("entry_engine") or data.get("event_type") or "unknown"
            except:
                pass
                
            strat_key = f"{mode}/{strategy}"
            if strat_key not in strategy_stats:
                strategy_stats[strat_key] = {"pnl": 0.0, "count": 0}
            strategy_stats[strat_key]["pnl"] += pnl
            strategy_stats[strat_key]["count"] += 1
            
        print(f"Total PnL: ${total_pnl:.2f}")
        print(f"Total Closed Positions: {len(rows)}")
        print(f"Wins: {wins}, Losses: {losses}")
        print(f"Win Rate: {wins/(wins+losses)*100:.1f}%" if (wins+losses) > 0 else "Win Rate: N/A")
        
        print("\n--- Strategy Breakdown ---")
        for strat, stats in strategy_stats.items():
            print(f"{strat}: ${stats['pnl']:.2f} ({stats['count']} trades)")
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    analyze_sqlite_pnl()
