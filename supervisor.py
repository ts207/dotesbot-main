"""Watchdog supervisor for the Dota/Polymarket bot AND the market binder.

Solves the failure that bit us repeatedly: a process either DIES (nohup not
surviving) or HANGS (PID alive but loop frozen — a zombie that silently stops
working for hours). BOTH the bot (main.py) and the binder (auto_series_binder.py)
zombied this way; a binder hang stalls the whole pipeline because nothing gets
bound. A PID check can't detect a hang, so each process rewrites a HEARTBEAT file
every loop iteration and this watchdog restarts on death OR heartbeat staleness.

Run as the persistent process; it owns BOTH children:
    python3 supervisor.py

Tunables via env: HANG_SECONDS (default per-process below), CHECK_EVERY (30).
"""
from __future__ import annotations
import os, sys, time, subprocess
from datetime import datetime, timezone

CHECK_EVERY = int(os.getenv("CHECK_EVERY", "30"))
SUPLOG = "logs/supervisor.log"

# name -> (launch argv, heartbeat file, hang threshold sec, startup grace sec, log file)
PROCS = {
    "bot":    ([sys.executable, "main.py"],
               "logs/heartbeat",         180, 90, "logs/stdout.log"),
    "binder": ([sys.executable, "auto_series_binder.py", "--loop"],
               "logs/binder_heartbeat",  150, 45, "logs/binder.log"),
    "shadow": ([sys.executable, "settlement_shadow.py", "--loop"],
               "logs/shadow_heartbeat",  450, 60, "logs/shadow.log"),
    # continuous health/risk watch — equity curve + alerts (logs/monitor.log).
    # Observability + the daily-drawdown executor circuit-breaker is the hard kill.
    "monitor": ([sys.executable, "monitor.py", "--loop"],
                "logs/monitor_heartbeat", 900, 60, "logs/monitor.log"),
}


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    try:
        with open(SUPLOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def hb_age(path: str) -> float:
    try:
        return time.time() - float(open(path).read().strip())
    except Exception:
        return 1e9


def kill_match(pat: str) -> None:
    os.system(f"pkill -9 -f '{pat}' 2>/dev/null")


def start(name: str):
    argv, hb, _, _, logf = PROCS[name]
    kill_match(argv[1])            # clear any stray instance of this script
    time.sleep(1)
    try:
        os.remove(hb)
    except OSError:
        pass
    out = open(logf, "a")
    p = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT)
    log(f"[supervisor] started {name} pid={p.pid}")
    return {"proc": p, "started": time.time()}


def main():
    log("[supervisor] starting — managing: " + ", ".join(PROCS))
    state = {name: start(name) for name in PROCS}
    counts = {name: 0 for name in PROCS}
    while True:
        time.sleep(CHECK_EVERY)
        for name, (argv, hb, hang_s, grace_s, logf) in PROCS.items():
            st = state[name]
            p = st["proc"]
            dead = p.poll() is not None
            warming = (time.time() - st["started"]) < grace_s
            age = hb_age(hb)
            hung = (not warming) and (age > hang_s)
            if dead or hung:
                reason = (f"DIED (exit={p.returncode})" if dead
                          else f"HUNG (heartbeat {age:.0f}s > {hang_s}s)")
                counts[name] += 1
                log(f"[supervisor] {name} {reason} — restart #{counts[name]}")
                try:
                    p.kill(); p.wait(timeout=10)
                except Exception:
                    pass
                state[name] = start(name)


if __name__ == "__main__":
    main()
