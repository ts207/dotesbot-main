import os
import sys
import time
import json
import sqlite3
import argparse
import subprocess
from config import ENABLE_REAL_LIVE_TRADING

HEARTBEATS = {
    "main": ("logs/heartbeat", 60),
    "binder": ("logs/binder_heartbeat", 60),
    "shadow": ("logs/shadow_heartbeat", 60),
    "monitor": ("logs/monitor_heartbeat", 600),
}

def check_process(name):
    try:
        out = subprocess.check_output(["pgrep", "-f", name]).decode()
        return len(out.strip().split("\n")) > 0
    except subprocess.CalledProcessError:
        return False

def check_heartbeats():
    now = time.time()
    stale = []
    for name, (path, max_age) in HEARTBEATS.items():
        if not os.path.exists(path):
            stale.append(f"{name} missing")
            continue
        age = now - os.path.getmtime(path)
        if age > max_age:
            stale.append(f"{name} stale ({int(age)}s)")
    return stale

def run_risk_audit(nav):
    try:
        out = subprocess.check_output([".venv/bin/python", "risk_config_audit.py", str(nav)], stderr=subprocess.STDOUT).decode()
        if "WARN" in out:
            return False, out
        return True, out
    except subprocess.CalledProcessError as e:
        return False, e.output.decode()
    except FileNotFoundError:
        try:
            out = subprocess.check_output(["python3", "risk_config_audit.py", str(nav)], stderr=subprocess.STDOUT).decode()
            if "WARN" in out:
                return False, out
            return True, out
        except:
            return False, "Failed to run risk_config_audit"

def run_outcome_audit():
    try:
        out = subprocess.check_output([".venv/bin/python", "outcome_attribution.py", "--audit-db"], stderr=subprocess.STDOUT).decode()
        return True, out
    except subprocess.CalledProcessError as e:
        return False, e.output.decode()
    except FileNotFoundError:
        try:
            out = subprocess.check_output(["python3", "outcome_attribution.py", "--audit-db"], stderr=subprocess.STDOUT).decode()
            return True, out
        except:
            return False, "Failed to run outcome_attribution"

def check_sqlite_readable(path):
    if not os.path.exists(path):
        return False
    try:
        with sqlite3.connect(path) as conn:
            conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return True
    except sqlite3.Error:
        return False

def check_positions(path):
    if not os.path.exists(path):
        return True
    try:
        with open(path) as f:
            data = json.load(f)
            if len(data) > 20: # Sane max open positions
                return False
        return True
    except:
        return False

def check_live_attempts(allow_real_live):
    if allow_real_live:
        return True
    path = "logs/live_attempts.csv"
    if not os.path.exists(path):
        return True
    if os.path.getsize(path) > 100: # Has data
        return False
    return True

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--nav", type=float, required=True)
    parser.add_argument("--allow-real-live", action="store_true")
    args = parser.parse_args()

    errors = []
    warnings = []

    if not check_process("supervisor.py"):
        errors.append("supervisor process missing")

    stale = check_heartbeats()
    for s in stale:
        if "monitor" in s or "shadow" in s:
            warnings.append(s)
        else:
            errors.append(s)

    if ENABLE_REAL_LIVE_TRADING and not args.allow_real_live:
        errors.append("ENABLE_REAL_LIVE_TRADING=true but --allow-real-live not passed")

    risk_ok, risk_out = run_risk_audit(args.nav)
    if not risk_ok:
        warnings.append(f"risk_config_audit warning: {risk_out.strip()}")

    outcome_ok, outcome_out = run_outcome_audit()
    if not outcome_ok:
        warnings.append("outcome_attribution --audit-db returned suspect rows")

    if not check_sqlite_readable("logs/state_v2.sqlite"):
        errors.append("logs/state_v2.sqlite not readable")

    if not check_positions("logs/paper_positions_v2.json"):
        errors.append("logs/paper_positions_v2.json not readable or too many open positions")

    if not check_live_attempts(args.allow_real_live):
        errors.append("live_attempts.csv has entries but real-live is false")

    if errors:
        print("CRITICAL")
        for e in errors:
            print(f"- {e}")
        for w in warnings:
            print(f"- [WARN] {w}")
        sys.exit(2)
    elif warnings or not args.allow_real_live:
        print("READY_FOR_PAPER_OBSERVATION")
        for w in warnings:
            print(f"- [WARN] {w}")
        sys.exit(0)
    else:
        print("READY_FOR_REAL_LIVE")
        sys.exit(0)

if __name__ == "__main__":
    main()
