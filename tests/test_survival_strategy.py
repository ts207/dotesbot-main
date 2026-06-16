from __future__ import annotations

from survival_strategy import audit_config, audit_decision, load_profile


def base_env() -> dict[str, str]:
    return {
        "ENABLE_REAL_LIVE_TRADING": "false",
        "VALUE_ENGINE_ENABLED": "true",
        "ENABLE_VALUE_TRADING": "true",
        "VALUE_MIN_EDGE": "0.15",
        "VALUE_MIN_FAIR": "0.70",
        "VALUE_MIN_PRICE": "0.55",
        "VALUE_MAX_PRICE": "0.84",
        "VALUE_MAX_EDGE": "0.25",
        "VALUE_MAX_GAME_TIME": "2400",
        "VALUE_MAX_BOOK_AGE_MS": "15000",
        "VALUE_CONFIRM_ENABLED": "false",
        "EVENT_DETECTORS_ENABLED": "false",
        "DSWING_ENABLED": "false",
        "MAX_TRADE_USD": "5",
        "VALUE_MAX_PER_MATCH": "5",
        "MAX_TOTAL_LIVE_USD": "30",
    }


def monitor(nav: float = 100.0) -> dict:
    return {"status": "ok", "nav_usd": nav, "alerts": []}


def source_state(status: str = "pass") -> dict:
    return {
        "status": status,
        "rows_read": 10,
        "required_source_rows": 10 if status == "pass" else 0,
        "missing_recommended_fields": [],
    }


def codes(findings) -> set[str]:
    return {item.code for item in findings}


def test_survival_profile_passes_clean_paper_config():
    findings = audit_config(env=base_env(), monitor=monitor(), profile=load_profile(), source_state=source_state())

    assert audit_decision(findings) == "GO_PAPER_SAFE"


def test_survival_profile_rejects_value_gate_drift():
    env = base_env()
    env["VALUE_MIN_FAIR"] = "0.60"

    findings = audit_config(env=env, monitor=monitor(), profile=load_profile(), source_state=source_state())

    assert audit_decision(findings) == "NO_GO"
    assert "value_min_fair_drift" in codes(findings)


def test_survival_profile_escalates_oversized_caps_when_live():
    env = base_env()
    env["ENABLE_REAL_LIVE_TRADING"] = "true"
    env["MAX_TRADE_USD"] = "20"
    env["VALUE_MAX_PER_MATCH"] = "20"
    env["MAX_TOTAL_LIVE_USD"] = "200"

    findings = audit_config(env=env, monitor=monitor(nav=50), profile=load_profile(), source_state=source_state())

    assert audit_decision(findings) == "NO_GO"
    assert {"real_live_enabled", "trade_size_high_for_nav", "value_cap_high_for_nav", "total_live_cap_high_for_nav"} <= codes(findings)


def test_survival_profile_rejects_event_allowlist_drift_when_events_on():
    env = base_env()
    env["EVENT_DETECTORS_ENABLED"] = "true"
    env["TRADE_EVENTS"] = "POLL_FIGHT_SWING"

    findings = audit_config(env=env, monitor=monitor(), profile=load_profile(), source_state=source_state())

    assert audit_decision(findings) == "NO_GO"
    assert "event_allowlist_drift" in codes(findings)


def test_survival_profile_warns_when_gettoplive_missing_in_paper():
    findings = audit_config(
        env=base_env(),
        monitor=monitor(),
        profile=load_profile(),
        source_state=source_state("no_required_source_recent"),
    )

    assert audit_decision(findings) == "PAPER_OR_OBSERVE"
    assert "gettoplive_state_no_required_source_recent" in codes(findings)


def test_survival_profile_rejects_missing_gettoplive_when_live():
    env = base_env()
    env["ENABLE_REAL_LIVE_TRADING"] = "true"

    findings = audit_config(
        env=env,
        monitor=monitor(),
        profile=load_profile(),
        source_state=source_state("no_required_source_recent"),
    )

    assert audit_decision(findings) == "NO_GO"
    assert "gettoplive_state_no_required_source_recent" in codes(findings)
