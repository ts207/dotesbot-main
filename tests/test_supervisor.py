from __future__ import annotations

import sys

import supervisor


def test_supervisor_procs_include_required_runtime_loops():
    assert set(supervisor.PROCS) == {"bot", "binder", "shadow", "monitor"}


def test_supervisor_each_process_has_heartbeat_and_log_file():
    for _, heartbeat, _, _, log_file in supervisor.PROCS.values():
        assert heartbeat.startswith("logs/")
        assert log_file.startswith("logs/")
        assert heartbeat != log_file


def test_supervisor_monitor_command_uses_current_python():
    argv, heartbeat, _, _, log_file = supervisor.PROCS["monitor"]
    assert argv == [sys.executable, "monitor.py", "--loop"]
    assert heartbeat == "logs/monitor_heartbeat"
    assert log_file == "logs/monitor.log"


def test_supervisor_shadow_command_uses_current_python():
    argv, heartbeat, _, _, log_file = supervisor.PROCS["shadow"]
    assert argv == [sys.executable, "settlement_shadow.py", "--loop"]
    assert heartbeat == "logs/shadow_heartbeat"
    assert log_file == "logs/settlement_shadow.log"


def test_monitor_imports_under_test_env():
    import monitor

    assert hasattr(monitor, "run")
