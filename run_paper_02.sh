#!/bin/bash
export PYTHONPATH=.
export MODEL_VALUE_REQUIRE_NET_WORTH=true
export MODEL_VALUE_MIN_EDGE=0.02
export MODEL_VALUE_CONFIRM_MIN_EDGE=0.02
export MODEL_VALUE_MIN_GAME_TIME_SEC=420
export MODEL_VALUE_MAX_GAME_TIME_SEC=2400
export LIVE_TRADING=false
export ENABLE_REAL_LIVE_TRADING=false

# Keep paper mode on live_parity to ensure only live-valid trades execute on paper
export PAPER_MODE=live_parity

echo "Starting paper soak at 0.02 threshold..."
.venv/bin/python3 supervisor.py
