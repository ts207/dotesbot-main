#!/usr/bin/env python3
import os
import sys
import json
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

def load_env():
    env = {}
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        with open(env_path, "r") as f:
            for line in f:
                clean_line = line.split("#")[0].strip()
                if clean_line:
                    parts = clean_line.split("=", 1)
                    if len(parts) == 2:
                        env[parts[0].strip()] = parts[1].strip()
    # Apply actual os environment variables
    for k, v in os.environ.items():
        env[k] = v
    return env

def main():
    env = load_env()
    
    safety_reasons = []
    data_reasons = []
    
    # 1. Strict parameter assertions
    expected_env = {
        "VALUE_MIN_EDGE": "0.15",
        "VALUE_MAX_EDGE": "0.25",
        "VALUE_MIN_PRICE": "0.55",
        "VALUE_MAX_PRICE": "0.84",
        "VALUE_MIN_FAIR": "0.70",
        "VALUE_MIN_GAME_TIME": "600",
        "VALUE_MAX_GAME_TIME": "2400",
        "VALUE_MAX_BOOK_AGE_MS": "15000"
    }
    
    for k, v in expected_env.items():
        val = env.get(k)
        if val != v:
            safety_reasons.append(f"Config drift: {k} changed from {v} to {val}")

    # Trading must be disabled
    enable_trading = env.get("ENABLE_REAL_LIVE_TRADING", "false").lower()
    enable_value_trading = env.get("ENABLE_VALUE_TRADING", "false").lower()
    emergency_stop_recommended = False
    if enable_trading != "false" or enable_value_trading != "false":
        safety_reasons.append("Trading flags are not fully false (ENABLE_REAL_LIVE_TRADING, ENABLE_VALUE_TRADING)")
        emergency_stop_recommended = True
        
    # Secondary alpha config checks
    config_path = REPO_ROOT / "configs" / "market_disagreement_alpha_v1.json"
    if not config_path.exists():
        safety_reasons.append("market_disagreement_alpha_v1.json missing")
    else:
        with open(config_path, "r") as f:
            alpha_config = json.load(f)
            
        for k, v in alpha_config.items():
            if not v.get("diagnostic_only", False):
                safety_reasons.append(f"Secondary alpha {k} diagnostic_only != true")
            if v.get("armed", True):
                safety_reasons.append(f"Secondary alpha {k} armed != false")

    # 5. File staleness
    STALE_THRESHOLD_SEC = 3600
    now = time.time()
    
    def check_stale(path_str, name):
        path = REPO_ROOT / path_str
        if not path.exists():
            data_reasons.append(f"{name} is missing")
        else:
            mtime = path.stat().st_mtime
            if now - mtime > STALE_THRESHOLD_SEC:
                data_reasons.append(f"{name} is stale ({(now - mtime)/60:.1f} mins old)")
                
    check_stale("logs/raw_snapshots.csv", "raw_snapshots.csv")
    check_stale("logs/book_events.csv", "book_events.csv")
    check_stale("logs/value_v1_shadow_forward_decisions.jsonl", "primary VALUE log")
    check_stale("logs/market_disagreement_alpha_shadow_decisions.jsonl", "secondary alpha log")

    # 6. Content checks in shadow logs
    causality_violations = 0
    stale_book_enters = 0
    degraded_source_enters = 0
    excessive_skew_enters = 0
    
    def scan_log(path_str, rule_key=None):
        nonlocal causality_violations, stale_book_enters, degraded_source_enters, excessive_skew_enters
        path = REPO_ROOT / path_str
        if not path.exists():
            return
            
        with open(path, "r") as f:
            for line in f:
                if not line.strip(): continue
                try: row = json.loads(line)
                except: continue
                
                decision = row.get("decision", "")
                is_enter = False
                if decision == "WOULD_ENTER":
                    is_enter = True
                elif row.get("alpha_would_enter"):
                    is_enter = True
                    
                if is_enter:
                    if not row.get("causality_valid", True):
                        causality_violations += 1
                    
                    if row.get("feed_skew_status") == "EXCESSIVE_SKEW":
                        excessive_skew_enters += 1
                        
                    age = row.get("book_age_ms", 0)
                    limit = 30000 if not rule_key else 15000
                    if age > limit:
                        stale_book_enters += 1
                    
                    if row.get("data_source", "top_live") != "top_live":
                        degraded_source_enters += 1

    scan_log("logs/value_v1_shadow_forward_decisions.jsonl")
    scan_log("logs/market_disagreement_alpha_shadow_decisions.jsonl", True)
    
    if causality_violations > 0:
        safety_reasons.append(f"causality violations > 0 ({causality_violations})")
    if excessive_skew_enters > 0:
        safety_reasons.append(f"excessive skew WOULD_ENTER > 0 ({excessive_skew_enters})")
    if stale_book_enters > 0:
        safety_reasons.append(f"stale-book WOULD_ENTER > 0 ({stale_book_enters})")
    if degraded_source_enters > 0:
        safety_reasons.append(f"degraded GetTopLive WOULD_ENTER > 0 ({degraded_source_enters})")
        
    safety_status = "NO_GO" if safety_reasons else "PASS"
    data_status = "WAITING_FOR_MARKET_DATA" if data_reasons else "ACTIVE"
    
    health = "PASS" if safety_status == "PASS" and data_status == "ACTIVE" else "NO_GO"
    clock = "STARTED" if health == "PASS" else "NOT_STARTED"
    
    state = "WAITING_FOR_MARKET_DATA" if data_status == "WAITING_FOR_MARKET_DATA" and safety_status == "PASS" else health

    print(f"SHADOW_HEALTH={health}")
    print(f"SHADOW_STATE={state}")
    print(f"VALIDATION_CLOCK={clock}")
    print(f"SAFETY_STATUS={safety_status}")
    print(f"DATA_STATUS={data_status}")
    
    if emergency_stop_recommended:
        stop_path = REPO_ROOT / "reports" / "shadow_forward_emergency_stop_required.json"
        stop_path.parent.mkdir(parents=True, exist_ok=True)
        with open(stop_path, "w") as f:
            json.dump({"emergency_stop_recommended": True, "reasons": safety_reasons}, f, indent=2)
            
    all_reasons = safety_reasons + data_reasons
    if all_reasons:
        print("\nReasons:")
        for r in all_reasons:
            print(f" - {r}")
            
    sys.exit(1 if health == "NO_GO" else 0)

if __name__ == "__main__":
    main()
