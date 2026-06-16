from __future__ import annotations

import json
import re
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_PROFILE_PATH = REPO_ROOT / "configs" / "survival_strategy_v1.json"


@dataclass(frozen=True)
class AuditFinding:
    level: str
    code: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"level": self.level, "code": self.code, "detail": self.detail}


def load_profile(path: Path | None = None) -> dict[str, Any]:
    profile_path = path or DEFAULT_PROFILE_PATH
    return json.loads(profile_path.read_text(encoding="utf-8"))


def load_json(rel: str, default: Any = None) -> Any:
    path = REPO_ROOT / rel
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def parse_env_file(path: Path | None = None) -> dict[str, str]:
    env_path = path or (REPO_ROOT / ".env")
    if not env_path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.split("#", 1)[0].strip().strip('"').strip("'")
    return out


def truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def clean_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\u2014", "-").replace("\u2013", "-")
    text = text.replace("\u2265", ">=").replace("\u2264", "<=")
    return text.encode("ascii", "ignore").decode("ascii")


def as_float(env: dict[str, str], key: str, default: float) -> float:
    try:
        return float(env.get(key, default))
    except (TypeError, ValueError):
        return default


def as_int(env: dict[str, str], key: str, default: int) -> int:
    try:
        return int(float(env.get(key, default)))
    except (TypeError, ValueError):
        return default


def latest_monitor_context(path: Path | None = None) -> dict[str, Any]:
    log_path = path or (REPO_ROOT / "logs" / "monitor.log")
    if not log_path.exists():
        return {"status": "missing"}
    lines = [ln.rstrip() for ln in log_path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    latest = ""
    alerts: list[str] = []
    for i in range(len(lines) - 1, -1, -1):
        if re.match(r"^\[\d\d:\d\d:\d\d\]", lines[i]):
            latest = clean_text(lines[i])
            alerts = [clean_text(ln.strip()) for ln in lines[i + 1 : i + 6] if "!!" in ln]
            break
    nav = None
    match = re.search(r"NAV \$(\d+(?:\.\d+)?)", latest)
    if match:
        nav = float(match.group(1))
    return {"status": "ok" if latest else "unparsed", "latest": latest, "nav_usd": nav, "alerts": alerts}


def _tail_lines(path: Path, max_lines: int, max_bytes: int = 8 * 1024 * 1024) -> list[str]:
    if not path.exists() or max_lines <= 0:
        return []
    file_size = path.stat().st_size
    with path.open("rb") as f:
        header = f.readline().decode("utf-8", errors="replace").rstrip("\r\n")
        if not header:
            return []
        read_size = min(file_size, max_bytes)
        f.seek(max(0, file_size - read_size))
        blob = f.read(read_size).decode("utf-8", errors="replace")
    lines = blob.splitlines()
    if file_size > read_size and lines:
        lines = lines[1:]
    return [header] + lines[-max_lines:]


def latest_gettoplive_state_audit(profile: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = profile if profile is not None else load_profile()
    req = profile.get("source_state_requirements", {})
    csv_path = REPO_ROOT / req.get("csv_path", "logs/raw_snapshots.csv")
    tail_rows = int(req.get("tail_rows", 2000))
    required_source = str(req.get("required_data_source", "top_live"))
    required_fields = list(req.get("required_fields", []))
    recommended_fields = list(req.get("recommended_fields", []))

    lines = _tail_lines(csv_path, tail_rows)
    if len(lines) <= 1:
        return {
            "status": "missing_or_empty",
            "csv_path": str(csv_path),
            "tail_rows_requested": tail_rows,
            "rows_read": 0,
            "required_data_source": required_source,
            "required_fields": required_fields,
            "recommended_fields": recommended_fields,
        }

    reader = csv.DictReader(lines)
    rows = [row for row in reader]
    source_counts: dict[str, int] = {}
    for row in rows:
        src = str(row.get("data_source") or "")
        source_counts[src] = source_counts.get(src, 0) + 1
    source_rows = [row for row in rows if str(row.get("data_source") or "") == required_source]

    def coverage(fields: list[str], sample: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        denom = len(sample)
        for field in fields:
            present = sum(1 for row in sample if str(row.get(field) or "").strip() != "")
            out[field] = {
                "present": present,
                "total": denom,
                "pct": round((present / denom * 100.0), 2) if denom else 0.0,
            }
        return out

    latest_ns = None
    latest_utc = None
    unique_matches = {str(row.get("match_id") or "") for row in source_rows if row.get("match_id")}
    for row in source_rows:
        try:
            ns = int(row.get("received_at_ns") or 0)
        except (TypeError, ValueError):
            ns = 0
        if latest_ns is None or ns > latest_ns:
            latest_ns = ns
            latest_utc = row.get("received_at_utc")

    required_cov = coverage(required_fields, source_rows)
    recommended_cov = coverage(recommended_fields, source_rows)
    missing_required = [field for field, stats in required_cov.items() if stats["present"] < stats["total"]]
    missing_recommended = [field for field, stats in recommended_cov.items() if stats["present"] < stats["total"]]
    minimum_rows = int(req.get("minimum_required_source_rows", 1))
    if len(source_rows) < minimum_rows:
        status = "no_required_source_recent"
    elif missing_required:
        status = "required_field_gaps"
    elif missing_recommended:
        status = "recommended_field_gaps"
    else:
        status = "pass"

    return {
        "status": status,
        "csv_path": str(csv_path),
        "tail_rows_requested": tail_rows,
        "rows_read": len(rows),
        "source_counts": source_counts,
        "required_data_source": required_source,
        "required_source_rows": len(source_rows),
        "required_source_unique_matches": len(unique_matches),
        "latest_required_source_received_at_ns": latest_ns,
        "latest_required_source_received_at_utc": latest_utc,
        "required_fields": required_cov,
        "recommended_fields": recommended_cov,
        "missing_required_fields": missing_required,
        "missing_recommended_fields": missing_recommended,
    }


def collect_evidence() -> dict[str, Any]:
    perf = load_json("reports/bot_performance_backtest_2026_06_07.json", {})
    model_b = load_json("reports/model_b_v2_corrected_diagnosis.json", {})
    replay = load_json("reports/replay_existing_signals_report.json", {})
    structure_audit = load_json("reports/gettoplive_structure_state_audit.json", {})
    survival_features = load_json("reports/value_survival_feature_audit.json", {})
    return {
        "value_settlement_backtest": perf.get("value_settlement_backtest", {}),
        "decisive_swing_ml_sniper_backtest": perf.get("decisive_swing_ml_sniper_backtest", {}),
        "gettoplive_structure_state_audit": structure_audit.get("totals", {}),
        "value_survival_feature_audit": {
            "baseline": survival_features.get("baseline", {}),
            "joined_summary": survival_features.get("joined_summary", {}),
            "join_coverage_pct": survival_features.get("join_coverage_pct"),
            "recommendation": survival_features.get("recommendation", {}),
        },
        "model_b_verdict": {
            "status": model_b.get("status"),
            "current_best_model": model_b.get("current_best_model"),
            "verdict": model_b.get("model_b_v2_verdict"),
        },
        "short_horizon_replay": replay.get("by_fill_assumption", {}),
    }


def _finding(level: str, code: str, detail: str) -> AuditFinding:
    return AuditFinding(level=level, code=code, detail=clean_text(detail))


def _gate_passes(actual: float, op: str, expected: float) -> bool:
    if op == ">=":
        return actual >= expected
    if op == "<=":
        return actual <= expected
    if op == "==":
        return actual == expected
    raise ValueError(f"unsupported profile gate op: {op}")


def audit_config(
    env: dict[str, str] | None = None,
    monitor: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    source_state: dict[str, Any] | None = None,
) -> list[AuditFinding]:
    env = env if env is not None else parse_env_file()
    monitor = monitor if monitor is not None else latest_monitor_context()
    profile = profile if profile is not None else load_profile()
    source_state = source_state if source_state is not None else latest_gettoplive_state_audit(profile)
    findings: list[AuditFinding] = []

    real_live = truthy(env.get("ENABLE_REAL_LIVE_TRADING"))
    value_profile = profile["positive_expectancy_lanes"]["value"]
    dswing_profile = profile["positive_expectancy_lanes"]["dswing"]
    bankroll = profile["bankroll_rules"]

    if real_live:
        findings.append(_finding("critical", "real_live_enabled", "Real live trading is enabled. Require manual funding/sizing approval and stable balance reads."))
    else:
        findings.append(_finding("info", "real_live_disabled", "Real live trading is disabled; strategy can observe or paper trade without capital risk."))

    if not truthy(env.get(value_profile["enabled_env"])):
        findings.append(_finding("critical", "value_engine_off", "The primary positive branch is VALUE; VALUE_ENGINE_ENABLED must stay true."))
    if not truthy(env.get(value_profile["trading_env"])):
        findings.append(_finding("warn", "value_trading_off", "VALUE entries are disabled. The primary strategy will only log rejects/signals."))

    for key, spec in value_profile["gates"].items():
        default = 0.0 if spec["op"] == ">=" else 999999.0
        actual = as_float(env, key, default)
        expected = float(spec["value"])
        if not _gate_passes(actual, spec["op"], expected):
            findings.append(_finding("critical", f"{key.lower()}_drift", f"{key}={actual:g}, expected {spec['op']} {expected:g} from {profile['profile_id']}."))

    for key in value_profile.get("must_be_false", []):
        if truthy(env.get(key)):
            findings.append(_finding("critical", f"{key.lower()}_enabled", f"{key}=true contradicts {profile['profile_id']}; current replay says confirmation worsened VALUE entries."))

    nav = monitor.get("nav_usd") if monitor else None
    if nav:
        max_trade = as_float(env, "MAX_TRADE_USD", 0.0)
        value_cap = as_float(env, "VALUE_MAX_PER_MATCH", 0.0)
        max_total = as_float(env, "MAX_TOTAL_LIVE_USD", 0.0)
        small_trade_cap = float(bankroll["default_small_bankroll_trade_cap_usd"])
        small_match_cap = float(bankroll["default_small_bankroll_value_match_cap_usd"])
        trade_limit = max(small_trade_cap, float(nav) * float(bankroll["max_trade_usd_fraction_of_nav"]))
        match_limit = max(small_match_cap, float(nav) * float(bankroll["max_trade_usd_fraction_of_nav"]))
        total_limit = float(nav) * float(bankroll["max_total_live_usd_fraction_of_nav"])
        level = "critical" if real_live else "warn"
        if max_trade > trade_limit:
            findings.append(_finding(level, "trade_size_high_for_nav", f"MAX_TRADE_USD=${max_trade:g} is aggressive versus latest monitor NAV ${nav:g}."))
        if value_cap > match_limit:
            findings.append(_finding(level, "value_cap_high_for_nav", f"VALUE_MAX_PER_MATCH=${value_cap:g} is aggressive versus latest monitor NAV ${nav:g}."))
        if max_total > total_limit:
            findings.append(_finding(level, "total_live_cap_high_for_nav", f"MAX_TOTAL_LIVE_USD=${max_total:g} exceeds {bankroll['max_total_live_usd_fraction_of_nav']:.0%} of latest monitor NAV ${nav:g}."))

    if truthy(env.get(profile["secondary_event_coverage"]["enabled_env"])):
        configured = {x.strip() for x in env.get("TRADE_EVENTS", "").split(",") if x.strip()}
        expected = set(profile["secondary_event_coverage"]["allowed_trade_events"])
        if configured != expected:
            findings.append(_finding("critical", "event_allowlist_drift", "EVENT_DETECTORS_ENABLED=true but TRADE_EVENTS does not exactly match the winner event set."))
        else:
            findings.append(_finding("warn", "event_detectors_on", "Winner event set is configured, but current short-horizon replay is fragile; use as secondary coverage only."))
    else:
        findings.append(_finding("info", "event_detectors_off", "Event detector trading is off; this avoids the fragile short-horizon/momentum paths."))

    if truthy(env.get(dswing_profile["enabled_env"])):
        level = "critical" if real_live else "warn"
        findings.append(_finding(level, "dswing_armed", "DSWING is armed. It is positive but small-n and depends on thin ML exit liquidity."))
    else:
        findings.append(_finding("info", "dswing_off", "DSWING is off; keep it off until explicitly armed with capital and series-state checks."))

    for alert in (monitor or {}).get("alerts", []):
        if "no book" in alert.lower() or "redeem" in alert.lower():
            findings.append(_finding("warn", "stale_position_needs_redeem", alert))

    source_status = source_state.get("status")
    if source_status in {"missing_or_empty", "no_required_source_recent", "required_field_gaps"}:
        level = "critical" if real_live else "warn"
        findings.append(_finding(
            level,
            f"gettoplive_state_{source_status}",
            f"GetTopLive state audit status={source_status}; source_rows={source_state.get('required_source_rows', 0)} rows_read={source_state.get('rows_read', 0)}.",
        ))
    elif source_status == "recommended_field_gaps":
        findings.append(_finding(
            "warn",
            "gettoplive_state_recommended_field_gaps",
            f"GetTopLive recommended fields missing: {','.join(source_state.get('missing_recommended_fields', []))}.",
        ))

    return findings


def audit_decision(findings: list[AuditFinding]) -> str:
    if any(item.level == "critical" for item in findings):
        return "NO_GO"
    if any(item.level == "warn" for item in findings):
        return "PAPER_OR_OBSERVE"
    return "GO_PAPER_SAFE"


def current_config(env: dict[str, str]) -> dict[str, str | None]:
    keys = [
        "ENABLE_REAL_LIVE_TRADING",
        "VALUE_ENGINE_ENABLED",
        "ENABLE_VALUE_TRADING",
        "VALUE_MIN_EDGE",
        "VALUE_MIN_FAIR",
        "VALUE_MIN_PRICE",
        "VALUE_MAX_PRICE",
        "VALUE_MAX_EDGE",
        "VALUE_MAX_GAME_TIME",
        "VALUE_CONFIRM_ENABLED",
        "EVENT_DETECTORS_ENABLED",
        "TRADE_EVENTS",
        "DSWING_ENABLED",
        "MAX_TRADE_USD",
        "VALUE_MAX_PER_MATCH",
        "MAX_TOTAL_LIVE_USD",
    ]
    return {key: env.get(key) for key in keys}


def build_report() -> dict[str, Any]:
    env = parse_env_file()
    monitor = latest_monitor_context()
    profile = load_profile()
    source_state = latest_gettoplive_state_audit(profile)
    findings = audit_config(env=env, monitor=monitor, profile=profile, source_state=source_state)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "decision": audit_decision(findings),
        "profile_id": profile["profile_id"],
        "current_config": current_config(env),
        "monitor_context": monitor,
        "source_state": source_state,
        "structure_state_policy": profile.get("structure_state_policy", {}),
        "evidence": collect_evidence(),
        "findings": [item.to_dict() for item in findings],
    }
