# Runbook — shadow → paper → live promotion

This runbook covers operating the bot through three stages:

1. **SHADOW** — engines run, every decision is logged, no orders submitted anywhere.
2. **PAPER** — engines submit through `live_executor` with `ENABLE_REAL_LIVE_TRADING=false`; PnL accrues against the paper-trader stand-in.
3. **LIVE** — real CLOB orders go to Polymarket.

Backtest expectation set: **+$11-12/day at $5-$10 base sizing on the joined 13-day dataset.** Of that, ~$6 from continuous and ~$6 from arb. Scalp is disabled (faithful backtest: −$0.97/trade).

---

## Stage 1 — SHADOW

**Goal:** observe the strategies running against live Polymarket + Steam data without risk.

### Preflight
```bash
python3 scripts/preflight.py
```
All 7 sections must show `✅`. If any fail, fix before starting.

### Verify shadow flags
Required in `.env`:
```
CONTINUOUS_ENGINE_ENABLED=true
ENABLE_CONTINUOUS_TRADING=false
ARB_ENGINE_ENABLED=true
ENABLE_ARB_TRADING=false
ENABLE_REAL_LIVE_TRADING=false
SCALP_ENABLED=false
```
(These are the current values; verify with `python3 scripts/preflight.py`.)

### Start
```bash
python3 supervisor.py
```

### What gets written

| File | What goes here in shadow mode |
|---|---|
| `logs/continuous_attempts.csv` | every continuous decision: would_trade=true rows = signals, would_trade=false = rejects with reason |
| `logs/arb_attempts.csv` | every arb decision: opportunities (would_trade=true) and rejects (with reason like below_min_profit, yes_ask_size_insufficient) |
| `logs/raw_snapshots.csv` | every Steam snapshot (unchanged from existing) |
| `logs/book_events.csv` | every Polymarket book WS update (unchanged) |
| `logs/live_attempts.csv` | **empty** — nothing submitted |

### Stop condition — wait at least 48h before promoting

48h gives both strategies enough samples to validate:

| Strategy | Expected shadow volume in 48h |
|---|---|
| Continuous | 25–30 would_trades |
| Arb | 30–40 opportunities |

### Shadow KPIs to check

```bash
# Continuous signals per day
awk -F, 'NR>1 && $5=="True" {split($1,a,"T"); print a[1]}' logs/continuous_attempts.csv | sort | uniq -c

# Continuous reject reasons (the funnel)
awk -F, 'NR>1 && $5=="False" {print $6}' logs/continuous_attempts.csv | sort | uniq -c | sort -rn

# Arb opportunities per day
awk -F, 'NR>1 && $5=="True" {split($1,a,"T"); print a[1]}' logs/arb_attempts.csv | sort | uniq -c

# Arb profit-cents distribution
awk -F, 'NR>1 && $5=="True" {print $13}' logs/arb_attempts.csv | sort -n | uniq -c
```

### Promotion gates (must all pass before stage 2)

| Gate | Threshold |
|---|---|
| Continuous signals over 48h | ≥ 30 |
| Continuous distribution: pregame_signed | non-degenerate (real markets being tracked) |
| Continuous reject reasons | dominant ones are `magnitude_below_floor`, `snap_gap_too_large` — these are normal. If you see lots of `book_unavailable`, the book stream is sick. |
| Arb opportunities over 48h | ≥ 30 |
| Arb profit_cents median | between 2c and 4c (matches backtest) |
| No `live_attempts.csv` rows | confirms shadow mode |

---

## Stage 2 — PAPER

**Goal:** verify the execution path end-to-end without real money. Paper trades use the fake CLOB client; PnL is computed against actual market prices.

### Promote
Edit `.env`:
```
ENABLE_CONTINUOUS_TRADING=true
ENABLE_ARB_TRADING=true
# leave ENABLE_REAL_LIVE_TRADING=false
```

Restart the bot.

### What changes
- `logs/paper_attempts.csv` starts accumulating rows tagged `trader_kind=continuous` or `trader_kind=arb` instead of live_attempts.csv.
- `logs/paper_positions.json` reflects opens/closes separated from the live positions file.
- Fills are simulated natively based on `best_ask` at submission time and tracked locally.
- Global risk and budget rules are applied correctly via `logs/paper_state.json`.

### Stop condition — wait at least 72h

72h gives roughly:

| Strategy | Expected paper volume in 72h |
|---|---|
| Continuous | 40-50 paper attempts |
| Arb | 50-60 paper attempts |

### Paper KPIs (verify per-strategy)

```bash
# Per-strategy attempt counts
awk -F, 'NR>1 {print $25}' logs/paper_attempts.csv | sort | uniq -c

# Continuous order_status distribution
awk -F, 'NR>1 && $25=="continuous" {print $26}' logs/paper_attempts.csv | sort | uniq -c

# Arb leg-pair completion (look for arb_id appearing twice per pair)
awk -F, 'NR>1 && $25=="arb" {print $28}' logs/paper_attempts.csv | sort | uniq -c | sort -rn | head
```

### Promotion gates (must all pass before stage 3)

| Gate | Threshold |
|---|---|
| Continuous paper $/trade | within ±50% of $0.42 backtest = [$0.21, $0.63] |
| Continuous win rate | ≥ 55% (vs 63% backtest, some live drift allowed) |
| Arb pair completion rate | ≥ 95% (both legs fill on the same arb) |
| Arb $/trade | within ±50% of $0.37 = [$0.18, $0.55] |
| No exception-status attempts | system errors must be resolved first |

---

## Stage 3 — LIVE

**Goal:** real CLOB orders. Real PnL.

### Pre-stage 3 checklist
- USDC balance in `POLY_FUNDER_ADDRESS` ≥ **$300** (covers worst-case 25 arbs × $10 + 5 continuous × $10 + buffer)
- Paper KPIs (Stage 2) all green
- You've watched 1+ trade cycles in paper and they look right
- You have a way to monitor the bot remotely (the `dashboard.py` or just `tail -f logs/live_attempts.csv`)

### Promote
Edit `.env`:
```
ENABLE_REAL_LIVE_TRADING=true
```

Other suggested live caps (already in `.env`):
```
MAX_TRADE_USD=50              # absolute per-trade ceiling
MAX_TOTAL_LIVE_USD=2000       # daily total submitted cap
MAX_DAILY_DRAWDOWN_USD=70     # circuit breaker
MAX_OPEN_POSITIONS=10         # concurrent across all strategies
```

Restart the bot.

### Live KPIs (check daily)

| Metric | Target | Read from |
|---|---|---|
| Trades per day | 25-35 | `live_attempts.csv` |
| Continuous $/trade | ≥ $0.20 | tagged `trader_kind=continuous` |
| Arb pair $/trade | ≥ $0.15 | tagged `trader_kind=arb` |
| Total daily PnL | ≥ $5 | sum of realized + unrealized |
| Open positions | ≤ 10 at all times | `live_positions.json` |
| USDC balance trend | flat-to-up | wallet |

### Daily checks

```bash
# Daily counters
date_today=$(date -u +%Y-%m-%d)
echo "=== $date_today ==="
echo "Continuous attempts today:"
grep "^$date_today" logs/live_attempts.csv | awk -F, '$25=="continuous"' | wc -l
echo "Arb attempts today:"
grep "^$date_today" logs/live_attempts.csv | awk -F, '$25=="arb"' | wc -l
echo "Exceptions today:"
grep "^$date_today" logs/live_attempts.csv | awk -F, '$26=="exception"' | wc -l
```

---

## Failure conditions — when to STOP

Hard stops (immediately edit `.env` and set `ENABLE_REAL_LIVE_TRADING=false`):

| Condition | Why |
|---|---|
| Daily realized PnL ≤ −$70 | hits `MAX_DAILY_DRAWDOWN_USD` circuit breaker |
| > 5 exception-status attempts in an hour | system error, not a strategy issue |
| Continuous $/trade < −$0.50 over 30+ trades | strategy isn't working live |
| Arb pair completion rate < 80% | execution problems |
| USDC balance < $100 | running out of capital |
| Any "delayed" orders unresolved for >5 minutes | sequencer or polling issue |

Soft warnings (don't stop, but investigate):

- Continuous trades concentrating on 1-2 matches all session (concentration risk)
- Arb opportunities dropping to <5/day for 2 days in a row (market microstructure changed)
- Same reject reason dominating > 80% of attempts (gate misconfigured)

---

## Rollback procedure

```bash
# 1. Stop trading
sed -i 's/ENABLE_REAL_LIVE_TRADING=true/ENABLE_REAL_LIVE_TRADING=false/' .env

# 2. Restart the bot (catches the new env)
pkill -f 'supervisor.py'
python3 supervisor.py &

# 3. Verify in logs
grep "ENABLE_REAL_LIVE_TRADING" .env
```

To go further (paper mode only):
```bash
sed -i 's/ENABLE_CONTINUOUS_TRADING=true/ENABLE_CONTINUOUS_TRADING=false/' .env
sed -i 's/ENABLE_ARB_TRADING=true/ENABLE_ARB_TRADING=false/' .env
```

To go fully shadow:
```bash
# Keep the engines on, disable all trading paths
sed -i 's/ENABLE_CONTINUOUS_TRADING=true/ENABLE_CONTINUOUS_TRADING=false/' .env
sed -i 's/ENABLE_ARB_TRADING=true/ENABLE_ARB_TRADING=false/' .env
sed -i 's/ENABLE_REAL_LIVE_TRADING=true/ENABLE_REAL_LIVE_TRADING=false/' .env
```

---

## Scaling up after live success

After 100 successful continuous trades with KPI within target:

```bash
# Bump base size 2×
sed -i 's/CONTINUOUS_TRADE_USD=5/CONTINUOUS_TRADE_USD=10/' .env
sed -i 's/ARB_TOTAL_CAPITAL_USD=10/ARB_TOTAL_CAPITAL_USD=20/' .env
```

USDC balance requirement becomes ~$600. Don't bump again without 100 more successful trades.

---

## Quick reference

| Action | Command |
|---|---|
| Preflight | `python3 scripts/preflight.py` |
| Start | `python3 supervisor.py` |
| Stop | `pkill -f 'supervisor.py'` |
| Recent decisions | `tail -50 logs/continuous_attempts.csv` |
| Recent arbs | `tail -50 logs/arb_attempts.csv` |
| Recent attempts | `tail -50 logs/live_attempts.csv` |
| Open positions | `cat logs/live_positions.json | python3 -m json.tool` |
| Test continuous scorer | `python3 -m pytest tests/test_continuous_scorer.py` |
| Test arb scanner | `python3 -m pytest tests/test_arb_scanner.py` |
| Re-run historical backtest | `python3 scripts/backtest_from_v2.py` |
| Backfill data_v2 | `python3 scripts/backfill_to_v2.py` |
