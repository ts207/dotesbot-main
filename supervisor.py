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
import os, sys, time, subprocess, signal
from datetime import datetime, timezone

CHECK_EVERY = int(os.getenv("CHECK_EVERY", "30"))
SUPLOG = "logs/supervisor.log"
LOCK_FILE = "logs/supervisor.lock"

# name -> (launch argv, heartbeat file, hang threshold sec, startup grace sec, log file)
PROCS = {
    "bot":    ([sys.executable, "main.py"],
               "logs/heartbeat",         180, 90, "logs/stdout.log"),
    "binder": ([sys.executable, "auto_series_binder.py", "--loop"],
               "logs/binder_heartbeat",  150, 45, "logs/binder.log"),
}

global_state = {}


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(SUPLOG), exist_ok=True)
        with open(SUPLOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def hb_age(path: str) -> float:
    try:
        return time.time() - float(open(path).read().strip())
    except Exception:
        return 1e9


def terminate_process(p: subprocess.Popen, logf_handle=None) -> None:
    if p.poll() is None:
        try:
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=5)
        except Exception:
            pass
    if logf_handle:
        try:
            logf_handle.close()
        except Exception:
            pass


def start(name: str):
    argv, hb, _, _, logf = PROCS[name]
    time.sleep(1)
    try:
        os.remove(hb)
    except OSError:
        pass
    os.makedirs(os.path.dirname(logf), exist_ok=True)
    out = open(logf, "a")
    
    # Platform-aware process group creation
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    p = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT, **kwargs)
    log(f"[supervisor] started {name} pid={p.pid}")
    return {"proc": p, "started": time.time(), "log_handle": out}


def handle_shutdown(signum, frame):
    log("[supervisor] received shutdown signal, cleaning up children")
    for name, st in global_state.items():
        if "proc" in st:
            terminate_process(st["proc"], st.get("log_handle"))
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass
    sys.exit(0)


def setup_lock_or_exit():
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    if os.path.exists(LOCK_FILE):
        try:
            pid = int(open(LOCK_FILE).read().strip())
            # Check if PID is alive
            if os.name == 'nt':
                # Quick check on Windows
                import ctypes
                PROCESS_QUERY_INFORMATION = 0x0400
                handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
                if handle != 0:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    log(f"[supervisor] already running with PID {pid}")
                    sys.exit(1)
            else:
                try:
                    os.kill(pid, 0)
                    log(f"[supervisor] already running with PID {pid}")
                    sys.exit(1)
                except OSError:
                    pass
        except Exception:
            pass
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))


def main():
    setup_lock_or_exit()
    
    # Catch SIGTERM and SIGINT for graceful shutdown
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    log("[supervisor] starting — managing: " + ", ".join(PROCS))
    for name in PROCS:
        global_state[name] = start(name)
        
    counts = {name: 0 for name in PROCS}
    
    try:
        while True:
            time.sleep(CHECK_EVERY)
            for name, (argv, hb, hang_s, grace_s, logf) in PROCS.items():
                st = global_state[name]
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
                    terminate_process(p, st.get("log_handle"))
                    global_state[name] = start(name)
    finally:
        handle_shutdown(None, None)

if __name__ == "__main__":
    main()
