import pandas as pd
import numpy as np

# Use the backup log since it contains today's actual live/paper signal history
try:
    df = pd.read_csv("logs/signals.csv.20260516_225605.bak")
except:
    df = pd.read_csv("logs/signals.csv")

print(f"Total signals in log: {len(df)}")

# 1. Skip Reason Breakdown
print("\n--- Skip Reasons by Event Type ---")
skips = df[df['decision'] == 'skip']
reason_counts = skips.groupby(['event_type', 'skip_reason']).size().unstack(fill_value=0)
print(reason_counts)

# 2. Accepted Signal Quality (Paper/Live attempts)
print("\n--- Accepted Signal Stats ---")
accepted = df[df['decision'].str.contains('buy|trade', case=False, na=False)]
if not accepted.empty:
    cols = ['event_type', 'executable_edge', 'lag', 'price_quality_score', 'ask', 'radiant_lead']
    print(accepted[cols].describe())
else:
    print("No accepted trades in this log file.")

# 3. Correlation: Edge vs. (Assumed) Outcome
# (Since we don't have PnL in this log, we look at edge and lag distribution)
if not accepted.empty:
    print("\n--- Mean Edge by Event Type ---")
    print(accepted.groupby('event_type')['executable_edge'].mean())

# 4. Lead-scaling impact check
print("\n--- Avg Lead for Accepted Signals ---")
if not accepted.empty:
    print(accepted.groupby('event_type')['radiant_lead'].mean())

