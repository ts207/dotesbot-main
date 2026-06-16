import pandas as pd
df = pd.read_csv("validations/backtest_2026_05_26_stomps_promoted.csv")
stomps = df[df["event_type"].isin(["POLL_DECISIVE_STOMP", "POLL_RAPID_STOMP"])]
print(stomps.groupby("event_type").agg(
    n=("event_type", "count"),
    mean_pnl_30s=("pnl_30s", "mean"),
    mean_pnl_settle=("pnl_settle", "mean"),
    total_pnl_settle=("pnl_settle", "sum")
))
