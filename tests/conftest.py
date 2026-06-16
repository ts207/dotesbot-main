import os


# Keep tests independent from local live/paper runtime settings in .env.
# config.py uses load_dotenv without override, so these values win during tests.
os.environ["LIVE_TRADING"] = "false"
os.environ["MIN_LAG"] = "0.08"
os.environ["MIN_EXECUTABLE_EDGE"] = "0.03"
os.environ["MAX_SPREAD"] = "0.15"
os.environ["DEFAULT_MAX_FILL_PRICE"] = "0.80"
os.environ["MAX_BOOK_AGE_MS"] = "750"
os.environ["MAX_STEAM_AGE_MS"] = "1500"
os.environ["MAX_SOURCE_UPDATE_AGE_SEC"] = "45"
os.environ["REQUIRE_TOP_LIVE_FOR_SIGNALS"] = "true"
# 2026-05-28 — Phase R rolled back NW_MOMENTUM. Phase A.1 dropped POLL_DECISIVE_STOMP
# (n=67, 40% win, -0.79% mean return on deep_data_study — anti-signal).
os.environ["TRADE_EVENTS"] = (
    "BASE_PRESSURE_T3_COLLAPSE,BASE_PRESSURE_T4,"
    "OBJECTIVE_CONVERSION_T2,OBJECTIVE_CONVERSION_T3,OBJECTIVE_CONVERSION_T4,"
    "POLL_BUYBACK_CAPITULATION,POLL_COMEBACK_RECOVERY,"
    "POLL_FIGHT_SWING,POLL_KILL_BURST_CONFIRMED,POLL_LATE_FIGHT_FLIP,"
    "POLL_STRUCTURAL_DOMINANCE,POLL_VALUE_DISAGREEMENT,THRONE_EXPOSED"
)
os.environ["DISABLE_STRUCTURE_TRADES"] = "false"
os.environ["REALTIME_STATS_ENABLED"] = "false"
# Pin tests to FAK so existing FakeLiveClient (no buy_gtc_limit method)
# keeps working. The production .env can use GTC; tests stay deterministic.
os.environ["ORDER_TYPE"] = "FAK"
