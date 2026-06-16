from types import SimpleNamespace

from main import _VALUE_CONFIRM_STATE, _normalized_entry_fill, _value_confirmation_passes


def test_normalized_entry_fill_prefers_filled_usd_over_ambiguous_shares():
    cost, shares, price = _normalized_entry_fill(
        filled_usd="50.0",
        filled_shares="76.923074",
        avg_fill_price="0.7",
        fallback_price="0.7",
    )

    assert cost == 50.0
    assert price == 0.7
    assert round(shares, 6) == 71.428571
    assert round(cost - shares * price, 6) == 0


def test_normalized_entry_fill_uses_shares_when_usd_missing():
    cost, shares, price = _normalized_entry_fill(
        filled_usd=None,
        filled_shares="7.142856",
        avg_fill_price="0.72",
        fallback_price="0.72",
    )

    assert round(cost, 6) == 5.142856
    assert shares == 7.142856
    assert price == 0.72


def test_value_confirmation_requires_second_stable_signal(monkeypatch):
    _VALUE_CONFIRM_STATE.clear()
    monkeypatch.setenv("VALUE_CONFIRM_ENABLED", "true")
    monkeypatch.setenv("VALUE_CONFIRM_MIN_EDGE", "0.12")
    monkeypatch.setenv("VALUE_CONFIRM_MAX_AGE_SEC", "90")
    monkeypatch.setenv("VALUE_CONFIRM_MAX_ASK_WORSEN", "0.02")

    first = SimpleNamespace(
        match_id="M1",
        token_id="TYES",
        side="YES",
        edge=0.13,
        ask=0.60,
        received_at_ns=1_000_000_000,
        signal_id="s1",
    )
    second = SimpleNamespace(
        match_id="M1",
        token_id="TYES",
        side="YES",
        edge=0.125,
        ask=0.61,
        received_at_ns=31_000_000_000,
        signal_id="s2",
    )

    ok, reason = _value_confirmation_passes(first)
    assert not ok
    assert reason == "value_confirm_armed"

    ok, reason = _value_confirmation_passes(second)
    assert ok
    assert reason.startswith("value_confirmed:")


def test_value_confirmation_blocks_weak_edge(monkeypatch):
    _VALUE_CONFIRM_STATE.clear()
    monkeypatch.setenv("VALUE_CONFIRM_ENABLED", "true")
    monkeypatch.setenv("VALUE_CONFIRM_MIN_EDGE", "0.12")

    weak = SimpleNamespace(
        match_id="M1",
        token_id="TYES",
        side="YES",
        edge=0.089,
        ask=0.66,
        received_at_ns=1_000_000_000,
        signal_id="s1",
    )

    ok, reason = _value_confirmation_passes(weak)
    assert not ok
    assert reason.startswith("value_confirm_edge_too_low:")
    assert _VALUE_CONFIRM_STATE == {}


def test_value_confirmation_requires_prior_strong_signal(monkeypatch):
    _VALUE_CONFIRM_STATE.clear()
    monkeypatch.setenv("VALUE_CONFIRM_ENABLED", "true")
    monkeypatch.setenv("VALUE_CONFIRM_MIN_EDGE", "0.12")
    monkeypatch.setenv("VALUE_CONFIRM_MAX_AGE_SEC", "90")
    monkeypatch.setenv("VALUE_CONFIRM_MAX_ASK_WORSEN", "0.02")

    weak = SimpleNamespace(
        match_id="M1",
        token_id="TYES",
        side="YES",
        edge=0.09,
        ask=0.66,
        received_at_ns=1_000_000_000,
        signal_id="weak",
    )
    strong = SimpleNamespace(
        match_id="M1",
        token_id="TYES",
        side="YES",
        edge=0.13,
        ask=0.65,
        received_at_ns=11_000_000_000,
        signal_id="strong",
    )

    ok, reason = _value_confirmation_passes(weak)
    assert not ok
    assert reason.startswith("value_confirm_edge_too_low:")

    ok, reason = _value_confirmation_passes(strong)
    assert not ok
    assert reason == "value_confirm_armed"
