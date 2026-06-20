from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal, Mapping

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency in prod only
    def load_dotenv(*args, **kwargs):
        return False


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class FeedConfig:
    steam_poll_seconds: float
    max_steam_age_ms: int
    max_source_update_age_sec: float
    require_top_live_for_signals: bool


@dataclass(frozen=True)
class BookConfig:
    max_book_age_ms: int
    max_spread: float
    min_ask_size_usd: float


@dataclass(frozen=True)
class SignalConfig:
    min_lag: float
    min_executable_edge: float


@dataclass(frozen=True)
class PaperConfig:
    paper_mode: Literal["research", "live_parity", "shadow_live"]
    paper_trade_size_usd: float
    paper_slippage_cents: float
    paper_execution_delay_ms: int
    max_open_usd_per_match: float


@dataclass(frozen=True)
class LiveConfig:
    live_mode: Literal["off", "dry_run", "real"]
    max_total_live_usd: float
    max_trade_usd: float
    max_open_positions: int
    max_daily_drawdown_usd: float

    @property
    def live_trading(self) -> bool:
        return self.live_mode in {"dry_run", "real"}

    @property
    def enable_real_live_trading(self) -> bool:
        return self.live_mode == "real"


@dataclass(frozen=True)
class StrategyConfig:
    value_enabled: bool
    dswing_enabled: bool
    event_triggered_value_enabled: bool
    model_value_enabled: bool


@dataclass(frozen=True)
class ConfigValue:
    setting: str
    value: Any
    source: Literal["env", "default"]
    safe_for_paper: bool
    safe_for_dry_live: bool
    safe_for_real_live: bool
    required_for_real_live: bool = False


@dataclass(frozen=True)
class RuntimeConfig:
    feed: FeedConfig
    book: BookConfig
    signal: SignalConfig
    paper: PaperConfig
    live: LiveConfig
    strategy: StrategyConfig
    values: tuple[ConfigValue, ...]

    def source_for(self, setting: str) -> Literal["env", "default"]:
        for value in self.values:
            if value.setting == setting:
                return value.source
        raise KeyError(setting)


DEFAULTS: dict[str, str] = {
    "STEAM_POLL_SECONDS": "0.5",
    "MAX_STEAM_AGE_MS": "1500",
    "MAX_SOURCE_UPDATE_AGE_SEC": "45",
    "REQUIRE_TOP_LIVE_FOR_SIGNALS": "true",
    "MAX_BOOK_AGE_MS": "750",
    "MAX_SPREAD": "0.06",
    "MIN_ASK_SIZE_USD": "25",
    "MIN_LAG": "0.08",
    "MIN_EXECUTABLE_EDGE": "0.03",
    "PAPER_MODE": "research",
    "PAPER_TRADE_SIZE_USD": "25",
    "PAPER_SLIPPAGE_CENTS": "0.01",
    "PAPER_EXECUTION_DELAY_MS": "0",
    "MAX_OPEN_USD_PER_MATCH": "150",
    "LIVE_TRADING": "false",
    "ENABLE_REAL_LIVE_TRADING": "false",
    "MAX_TOTAL_LIVE_USD": "10",
    "MAX_TRADE_USD": "1",
    "MAX_OPEN_POSITIONS": "1",
    "MAX_DAILY_DRAWDOWN_USD": "10",
    "VALUE_ENGINE_ENABLED": "true",
    "DSWING_ENABLED": "true",
    "EVENT_TRIGGERED_VALUE_ENABLED": "true",
    "MODEL_VALUE_ENABLED": "false",
}


REQUIRED_REAL_SETTINGS = {
    "MAX_TOTAL_LIVE_USD",
    "MAX_TRADE_USD",
    "MAX_OPEN_POSITIONS",
    "MAX_DAILY_DRAWDOWN_USD",
    "MAX_STEAM_AGE_MS",
    "MAX_SOURCE_UPDATE_AGE_SEC",
    "MAX_BOOK_AGE_MS",
    "MAX_SPREAD",
    "MIN_ASK_SIZE_USD",
    "MIN_LAG",
    "MIN_EXECUTABLE_EDGE",
}


def _raw(env: Mapping[str, str], key: str) -> tuple[str, Literal["env", "default"]]:
    if key == "MIN_LAG":
        value = env.get("MIN_LAG")
        if value is None or str(value).strip() == "":
            legacy = env.get("MIN_EDGE")
            if legacy is not None and str(legacy).strip() != "":
                return str(legacy).strip(), "env"
    value = env.get(key)
    if value is None or str(value).strip() == "":
        return DEFAULTS[key], "default"
    return str(value).strip(), "env"


def _float(env: Mapping[str, str], key: str) -> tuple[float, Literal["env", "default"]]:
    value, source = _raw(env, key)
    try:
        return float(value), source
    except ValueError as exc:
        raise RuntimeError(f"{key} must be a float, got {value!r}") from exc


def _int(env: Mapping[str, str], key: str) -> tuple[int, Literal["env", "default"]]:
    value, source = _raw(env, key)
    try:
        return int(float(value)), source
    except ValueError as exc:
        raise RuntimeError(f"{key} must be an integer, got {value!r}") from exc


def _bool(env: Mapping[str, str], key: str) -> tuple[bool, Literal["env", "default"]]:
    value, source = _raw(env, key)
    normalized = value.lower()
    if normalized in TRUE_VALUES:
        return True, source
    if normalized in FALSE_VALUES:
        return False, source
    raise RuntimeError(f"{key} must be boolean, got {value!r}")


def _paper_mode(env: Mapping[str, str], key: str) -> tuple[Literal["research", "live_parity", "shadow_live"], Literal["env", "default"]]:
    value, source = _raw(env, key)
    normalized = value.lower()
    if normalized not in {"research", "live_parity", "shadow_live"}:
        raise RuntimeError(f"{key} must be research, live_parity, or shadow_live; got {value!r}")
    return normalized, source  # type: ignore[return-value]


def _live_mode(env: Mapping[str, str]) -> tuple[Literal["off", "dry_run", "real"], Literal["env", "default"]]:
    real_live, real_source = _bool(env, "ENABLE_REAL_LIVE_TRADING")
    live_trading, live_source = _bool(env, "LIVE_TRADING")
    if real_live:
        return "real", real_source
    if live_trading:
        return "dry_run", live_source
    return "off", "env" if real_source == "env" or live_source == "env" else "default"


def _safe(setting: str, value: Any, source: Literal["env", "default"]) -> tuple[bool, bool, bool]:
    if setting == "MAX_STEAM_AGE_MS":
        return value <= 60_000, value <= 45_000, value <= 25_000
    if setting == "MAX_SOURCE_UPDATE_AGE_SEC":
        return value <= 120, value <= 60, value <= 45
    if setting == "MAX_BOOK_AGE_MS":
        return value <= 90_000, value <= 15_000, value <= 15_000
    if setting == "MAX_SPREAD":
        return value <= 0.50, value <= 0.15, value <= 0.15
    if setting == "MIN_ASK_SIZE_USD":
        return value >= 0, value >= 1, value >= 1
    if setting == "MIN_LAG":
        return value >= 0, value >= 0, value >= 0
    if setting == "MIN_EXECUTABLE_EDGE":
        return value >= 0, value >= 0, value >= 0
    if setting == "MAX_TOTAL_LIVE_USD":
        return True, value > 0, value > 0 and source == "env"
    if setting == "MAX_TRADE_USD":
        return True, value > 0, value > 0 and source == "env"
    if setting == "MAX_OPEN_POSITIONS":
        return True, value > 0, value > 0 and source == "env"
    if setting == "MAX_DAILY_DRAWDOWN_USD":
        return True, value > 0, value > 0 and source == "env"
    return True, True, True


def _tracked(
    env: Mapping[str, str],
    key: str,
    parser,
    rows: list[ConfigValue],
) -> Any:
    value, source = parser(env, key)
    safe_for_paper, safe_for_dry_live, safe_for_real_live = _safe(key, value, source)
    rows.append(
        ConfigValue(
            setting=key,
            value=value,
            source=source,
            safe_for_paper=safe_for_paper,
            safe_for_dry_live=safe_for_dry_live,
            safe_for_real_live=safe_for_real_live,
            required_for_real_live=key in REQUIRED_REAL_SETTINGS,
        )
    )
    return value


def load_config(
    env: Mapping[str, str] | None = None,
    *,
    validate_real_live: bool = True,
) -> RuntimeConfig:
    if env is None:
        load_dotenv()
        env = os.environ

    rows: list[ConfigValue] = []

    steam_poll_seconds = _tracked(env, "STEAM_POLL_SECONDS", _float, rows)
    max_steam_age_ms = _tracked(env, "MAX_STEAM_AGE_MS", _int, rows)
    max_source_update_age_sec = _tracked(env, "MAX_SOURCE_UPDATE_AGE_SEC", _float, rows)
    require_top_live_for_signals = _tracked(env, "REQUIRE_TOP_LIVE_FOR_SIGNALS", _bool, rows)

    max_book_age_ms = _tracked(env, "MAX_BOOK_AGE_MS", _int, rows)
    max_spread = _tracked(env, "MAX_SPREAD", _float, rows)
    min_ask_size_usd = _tracked(env, "MIN_ASK_SIZE_USD", _float, rows)

    min_lag = _tracked(env, "MIN_LAG", _float, rows)
    min_executable_edge = _tracked(env, "MIN_EXECUTABLE_EDGE", _float, rows)

    paper_mode = _tracked(env, "PAPER_MODE", _paper_mode, rows)
    paper_trade_size_usd = _tracked(env, "PAPER_TRADE_SIZE_USD", _float, rows)
    paper_slippage_cents = _tracked(env, "PAPER_SLIPPAGE_CENTS", _float, rows)
    paper_execution_delay_ms = _tracked(env, "PAPER_EXECUTION_DELAY_MS", _int, rows)
    max_open_usd_per_match = _tracked(env, "MAX_OPEN_USD_PER_MATCH", _float, rows)

    live_mode, live_mode_source = _live_mode(env)
    live_safe = _safe("LIVE_MODE", live_mode, live_mode_source)
    rows.append(
        ConfigValue(
            setting="LIVE_MODE",
            value=live_mode,
            source=live_mode_source,
            safe_for_paper=live_safe[0],
            safe_for_dry_live=live_safe[1],
            safe_for_real_live=live_safe[2],
        )
    )
    max_total_live_usd = _tracked(env, "MAX_TOTAL_LIVE_USD", _float, rows)
    max_trade_usd = _tracked(env, "MAX_TRADE_USD", _float, rows)
    max_open_positions = _tracked(env, "MAX_OPEN_POSITIONS", _int, rows)
    max_daily_drawdown_usd = _tracked(env, "MAX_DAILY_DRAWDOWN_USD", _float, rows)

    value_enabled = _tracked(env, "VALUE_ENGINE_ENABLED", _bool, rows)
    dswing_enabled = _tracked(env, "DSWING_ENABLED", _bool, rows)
    event_triggered_value_enabled = _tracked(env, "EVENT_TRIGGERED_VALUE_ENABLED", _bool, rows)
    model_value_enabled = _tracked(env, "MODEL_VALUE_ENABLED", _bool, rows)

    config = RuntimeConfig(
        feed=FeedConfig(
            steam_poll_seconds=steam_poll_seconds,
            max_steam_age_ms=max_steam_age_ms,
            max_source_update_age_sec=max_source_update_age_sec,
            require_top_live_for_signals=require_top_live_for_signals,
        ),
        book=BookConfig(
            max_book_age_ms=max_book_age_ms,
            max_spread=max_spread,
            min_ask_size_usd=min_ask_size_usd,
        ),
        signal=SignalConfig(
            min_lag=min_lag,
            min_executable_edge=min_executable_edge,
        ),
        paper=PaperConfig(
            paper_mode=paper_mode,
            paper_trade_size_usd=paper_trade_size_usd,
            paper_slippage_cents=paper_slippage_cents,
            paper_execution_delay_ms=paper_execution_delay_ms,
            max_open_usd_per_match=max_open_usd_per_match,
        ),
        live=LiveConfig(
            live_mode=live_mode,
            max_total_live_usd=max_total_live_usd,
            max_trade_usd=max_trade_usd,
            max_open_positions=max_open_positions,
            max_daily_drawdown_usd=max_daily_drawdown_usd,
        ),
        strategy=StrategyConfig(
            value_enabled=value_enabled,
            dswing_enabled=dswing_enabled,
            event_triggered_value_enabled=event_triggered_value_enabled,
            model_value_enabled=model_value_enabled,
        ),
        values=tuple(rows),
    )

    if validate_real_live and config.live.live_mode == "real":
        missing = [
            row.setting
            for row in config.values
            if row.required_for_real_live and (row.source == "default" or not row.safe_for_real_live)
        ]
        if missing:
            joined = ", ".join(sorted(missing))
            raise RuntimeError(f"real live mode requires explicit safe values for: {joined}")

    return config
