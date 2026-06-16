#!/usr/bin/env python3
import json, os
os.chdir("/home/tstuv/dota-poly-signal-pnl-asd")
with open("reports/bot_performance_backtest_2026_06_07.json") as f:
    d = json.load(f)
out = json.dumps(d, indent=2, default=str)
print(out[:8000])
