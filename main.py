from __future__ import annotations

from runtime import bot_runtime as _bot_runtime
from runtime.bot_runtime import BotRuntime
from runtime_config import load_config

_best_signal_candidate = _bot_runtime._best_signal_candidate
_exit_adverse_position_for_signal = _bot_runtime._exit_adverse_position_for_signal
_hybrid_context = _bot_runtime._hybrid_context
_hybrid_delay_seconds = _bot_runtime._hybrid_delay_seconds
_normalized_entry_fill = _bot_runtime._normalized_entry_fill
_value_confirmation_passes = _bot_runtime._value_confirmation_passes
_yes_fair_from_radiant = _bot_runtime._yes_fair_from_radiant
_VALUE_CONFIRM_STATE = _bot_runtime._VALUE_CONFIRM_STATE
_LOCK_HANDLE = None


def _acquire_single_instance_lock(path: str = "logs/paper_bot.lock") -> bool:
    ok = _bot_runtime._acquire_single_instance_lock(path)
    globals()["_LOCK_HANDLE"] = _bot_runtime._LOCK_HANDLE
    return ok


def main() -> None:
    cfg = load_config()
    BotRuntime(cfg).run()


if __name__ == "__main__":
    main()
