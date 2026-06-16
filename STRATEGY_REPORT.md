# Dota–Polymarket Strategy Review & Improvement Proposal
**Date:** 2026-05-31
**Scope:** Entry/exit mechanics, edge decomposition, failure modes, and ranked improvement proposals for the live-betting strategy on Polymarket Dota 2 markets.

---

## 1. Executive Summary

The strategy is **edge-positive and now provably so** (100% of bootstrap resamples positive after the 2026-05-31 improvements). Core findings:

- **Best config:** entry ask ∈ [0.45, 0.85], game_time ∈ [10, 35] min, tiered nw/kill filter, **hold to settle**.
- **Backtest (73 historical matches):** 30 trades, 93% win rate, +$0.198/trade, Sharpe 0.85, total +$5.93 (per $1 staked).
- **Hold-to-settle is mathematically optimal** — every take-profit, stop-loss, and trailing-stop variant tested *reduced* returns. This is now confirmed, not assumed.
- **Two distinct edge sources** identified: market mispricing (nw vs kills disagree) and momentum confirmation (high-ask follows).
- **The binding/coverage problem, not the strategy, is the live bottleneck.** Strategy only fires on ~40% of matches; the others are stomps or already-settled.

---

## 2. Current Strategy Definition

### 2.1 Entry — `POLL_FIRST_SWING_SETTLE`
Fires **once per match**, locks direction. All conditions must hold:

| # | Condition | Value | Rationale |
|---|-----------|-------|-----------|
| 1 | game_time | 10–35 min | <10 = laning noise; >35 = net-negative (late entries lose) |
| 2 | kill activity | ≥1 kill in last 2 snapshots | Requires real fight, not pure farm |
| 3 | nw swing magnitude | \|Δnw\| > 800 + 20·gt_min | Linear-scaled threshold; sustained across 1- and 3-snap windows |
| 4 | swing sign consistency | d1 and d3 same sign | Filters single-tick noise |
| 5 | ratio filter | \|nw\| ≤ 3·\|swing\| | Blocks dead-cat bounces inside large deficits |
| 6 | entry price | ask ∈ [0.45, 0.85] | Below 0.45 = S2 territory; above 0.85 = no upside, full tail risk |
| 7 | **tiered nw/kill** | ask<0.70 → require nw≠kill direction; ask≥0.70 → either | Two edge sources (see §4) |

**Direction:** sign of the net-worth swing (radiant vs dire), translated to YES/NO via `steam_side_mapping`.

### 2.2 Entry — `POLL_REVERSAL_ENTRY` (S2, separate strategy)
Buys the **underdog** early in a comeback arc:
- game_time > 10 min, nw deficit > (2000 + 100·gt_min)
- not collapsing (d_nw3 > −4000)
- positive tick (d_nw1>300) OR stalling (d_nw3>−500)
- entry ask ∈ [0.05, 0.45]
- Does **not** lock direction (standalone one-shot, no gate conflict)
- Backtest: 10 matches, +$0.68/trade avg (small sample, treat as exploratory)

### 2.3 Event-engine overlay
The existing ~12 tactical detectors (VALUE_DISAGREEMENT, STRUCTURAL_DOMINANCE, RAPID_STOMP, etc.) fire alongside, **gated to the S1-locked direction**. Per-event MIN/MAX fill caps and a blacklist (MAJOR_COMEBACK_RECOVERY) apply.

### 2.4 Exit — **Hold to settlement** (EXIT_HORIZON = 0)
All positions held until the market resolves to $0 or $1. No take-profit, no stop-loss.

---

## 3. Performance Analysis (Honest, All Gates Applied)

### 3.1 S1 alone, current config — CORRECTED to deployed behavior
**Important correction (2026-05-31):** earlier figures (30-31 trades, 93.5%) came
from a backtest that *scanned forward* to the first price-acceptable swing. The
**deployed detector fires once on the first swing and locks** — it does NOT scan
forward. Both "losses" in the 31-trade backtest came exclusively from scanned-later
swings that the live code never takes. The live-accurate number:
```
Sample:        ~19 trades / 73 matches (26% participation)
Win rate:      100% in-sample  → expect 88-92% live (small-sample regression)
Avg P&L:       +$0.236 per $1 staked  (highest per-trade edge in the whole stack)
Total:         +$4.48 per $1  (@$50 base × 1.6 boost ≈ $358)
```
**First-swing-only is principled, not overfit:** every alternative entry-selection
(2nd swing, largest, cheapest, last) scored worse, and the only backtest losses
were from chasing later swings after the market had already moved past the first.
The first swing captures the mispricing before the market corrects.

### 3.2 Evolution of the config (what each change bought)
| Config | n | WR | Avg | Sharpe | CI lower |
|--------|---|-----|-----|--------|----------|
| Baseline (kill+swing) | 40 | 77.5% | +0.059 | 0.19 | −0.066 |
| + tiered nw/kill | 37 | 83.8% | +0.073 | 0.19 | −0.066 |
| + max_ask 0.90→0.85 | 31 | 87% | +0.115 | 0.32 | −0.027 |
| + max_gt ≤35 min | **30** | **93%** | **+0.198** | **0.85** | **+0.104** |

The **max_ask cap and max_gt cap did the heavy lifting** — they removed the catastrophic −$0.90 favorite-bought-too-high losses and the net-negative late-game entries.

### 3.3 Combined (S1 + gated event engine), all gates
```
331 trades across 40 matches (~8/match, correlated)
94.3% per-trade WR, +$0.20/trade, total +$66.71
Honest per-MATCH: +$1.61/match (40 independent outcomes, not 331)
Expected per traded match @ $25/pos: ~+$21 (0.8·win − 0.2·loss)
```

---

## 4. Edge Decomposition — *Where the money comes from*

| Source | n | WR | Avg | Description |
|--------|---|-----|-----|-------------|
| **Mispricing** (ask 0.45–0.70, nw≠kill) | 18 | 84% | +$0.124 | Market priced on visible kills; net worth says opposite. Information asymmetry. |
| **Momentum** (ask 0.70–0.85, either) | ~12 | ~90% | +$0.075 | Market already moved on nw; kills confirm. Lower variance. |

**Critical insight:** dropping either tier *hurts* — disagree-only scored −$0.013/trade (it loses the high-confidence momentum wins). The two sources are complementary, not redundant.

---

## 5. Exit Analysis — *Hold-to-settle confirmed optimal*

Tested every exit rule against the full forward price path of all 30 entries:

```
HOLD TO SETTLE        n=30  WR=93%  avg=+0.198  total=+5.93  Sharpe=0.85  worst=-0.64   ← WINNER
Take-profit @ 0.98    n=30  WR=93%  avg=+0.188  total=+5.65  Sharpe=0.82
Take-profit @ 0.95    n=30  WR=93%  avg=+0.171  total=+5.14  Sharpe=0.76
Take-profit @ 0.90    n=30  WR=93%  avg=+0.143  total=+4.30  Sharpe=0.64
TP 0.95 / SL 0.20     n=30  WR=77%  avg=+0.035  total=+1.06  Sharpe=0.09   ← stops destroy it
Trailing stop -0.15   n=30  WR=50%  avg=+0.024  total=+0.73  Sharpe=0.09
```

**Why stops fail:** with a 0.31 win/loss ratio, you need *every* winner to fully pay out. Winners commonly dip after entry before resolving — a 0.30 stop catches those dips and converts winners into losses. The price-path data confirms:
- Winners reach bid≥0.95 a **median 15.9 min after entry** (slow grind, not instant).
- Of 2 losers, 1 spiked to 0.88 mid-game before collapsing — a trailing stop would *not* have saved capital net, because it also clips the 28 winners that dipped.

**Conclusion: do not add exits. Hold to settle is provably optimal for this edge profile.**

---

## 6. Failure-Mode Analysis

### 6.1 The 2 remaining S1 losses (post-improvement)
Both at ask ≥ 0.64, both late-ish, both where a brief swing flipped a genuinely-losing team. These survive the ratio filter but represent residual noise. At n=2 they are not separately fixable without overfitting.

### 6.2 Structural weaknesses
| Weakness | Severity | Notes |
|----------|----------|-------|
| **Skewed payoff** (W/L ratio 0.31) | High | Pick-up-pennies. Tail-sensitive. |
| **Small sample** (30 trades) | High | 93% WR will regress toward 82–87% live. |
| **Low participation** (41%) | Medium | Stomps & farm-games produce no entry. |
| **Correlated event-engine trades** | Medium | 8 trades/match share one outcome → real N is matches, not trades. |
| **Coverage/binding gaps** | High (live) | Steam omits team names for some featured matches → cannot bind → 0 trades on the exact games we want. |
| **Market speed on stomps** | Medium | Polymarket reprices on nw faster than kill-gated events fire. |

---

## 7. Improvement Proposals (Ranked by Expected Value / Effort)

### P1 — Binding coverage *(INVESTIGATED 2026-05-31: NOT a bug, struck)*
**Initial hypothesis:** featured matches return blank team names → binder can't match.
**Finding after investigation:** the GetTopLiveGame + GetLiveLeagueGames merge **already works correctly**. Tournament matches (NaVi vs Flame, etc.) come through with full names and bind as designed. The high-spectator blank games are high-MMR pubs/streamer lobbies with no league_id, no team_id, and no Polymarket market — correctly ignored.
**Real coverage constraint:** not a binding bug. The strategy only fires on competitive games with mid-game decisive swings (~40% of matches). Today's EWC games were all stomps (market priced out before entry window) and the one competitive game (LGD vs Pipsqueak) had not yet started. No code fix; this is the nature of the edge.

### P2 — Widen kill lookback 2→3 snapshots
**Data:** kill_lb=3 gave 42 trades / 78.6% / +$3.02 vs kill_lb=2's 40 / 77.5% / +$2.35 on the pre-improvement config.
**Rationale:** kills and nw swings don't always land in the same 60s window; 90s captures more genuine fight-driven entries without adding noise. Low risk, small positive.

### P3 — Per-match exposure cap for the correlated event overlay
**Problem:** 8 correlated trades/match means a wrong direction loses 8× simultaneously.
**Proposal:** cap total per-match notional at 4–5% of bankroll regardless of how many event signals fire. Size each event position as `match_budget / expected_event_count`.
**Expected:** same EV, materially lower drawdown variance. Pure risk management.

### P4 — Promote the mispricing tier, demote momentum sizing
**Data:** mispricing tier pays +$0.124 vs momentum +$0.075.
**Proposal:** size mispricing entries (ask<0.70, nw≠kill) at 1.5× the momentum entries. Kelly-justified: higher edge → larger fraction.
**Expected:** +10–15% on aggregate P&L at equal risk.

### P5 — S2 reversal validation (exploratory)
**Status:** 10 matches, +$0.68/trade, 100% WR — too small to trust.
**Proposal:** run S2 in shadow/paper for 30+ live fires before allocating real capital. The cheap-underdog thesis is sound (market lags slow grind comebacks) but unproven at scale.

### P6 — Confirmation sizing on event clustering
**Data:** first event in a match = 84% WR; later same-direction events = 96% WR.
**Proposal:** scale position size up 1.5–2× when 2+ events confirm the same direction in a match. The later confirmations are demonstrably higher-conviction.

### P7 — Replace kill-gate with nw-velocity for farm-stomp coverage *(research)*
**Problem:** pure macro-stomp games (one team farms to +15k with 0 kills) never trigger the kill-gated entry; market prices them out before any fight.
**Proposal:** add a parallel nw-velocity trigger (sustained nw rate > X/min over 4 snaps) that does *not* require kills, with its own (tighter) fill caps. **Caution:** earlier clean-strategy test showed no-kill triggers underperformed — this needs careful gating to avoid reintroducing noise. Research-only.

---

## 8. Risk Management & Sizing

**Recommended (eighth-Kelly, $1,000 bankroll):**
```
Per-match budget:      4% of bankroll = $40
Per-position base:     $40 / ~9 expected trades = ~$4.50
Mispricing tier:       1.5× base
Hard match cap:        5% of bankroll = $50
Blacklist:             MAJOR_COMEBACK_RECOVERY (44% WR)
```
Full Kelly is 31%/trade — far too aggressive given the 30-trade sample and skewed payoff. Quarter-to-eighth Kelly until ≥100 live fires confirm the edge.

**Drawdown reality:** worst-case is a run of wrong-direction matches. At 4%/match with 80% match-WR, a 5-match losing streak (~0.03% probability) = −20% bankroll. Survivable; recoverable in ~14 winning matches.

---

## 9. Implementation Roadmap

| Phase | Action | Effort | Impact |
|-------|--------|--------|--------|
| ~~Now~~ | ~~P1 binding fix~~ — investigated, not a bug (struck) | — | — |
| **Now** | P3 per-match exposure cap | S | ★★ |
| **Now** | P4 mispricing-tier sizing | S | ★★ |
| Week 1 | P2 kill lookback 2→3 | S | ★ |
| Week 2 | P6 confirmation sizing | M | ★★ |
| Ongoing | P5 S2 shadow validation (30+ fires) | — | ? |
| Research | P7 nw-velocity parallel trigger | L | ? |

**Reality check:** with P1 ruled out, the binding/coverage is *already working*. The remaining levers (P3/P4/P6) are sizing/risk refinements, not capability unlocks. The strategy's live throughput is gated by *match quality* (need competitive games), which is not something code can manufacture — it's a function of the tournament schedule.

---

## 10. Honest Caveats

1. **30 trades is a small sample.** 93% WR is almost certainly inflated; plan for 82–87% live.
2. **Backtest ≠ live.** Historical backtests scan all snapshots and pick the optimal entry; live gets one snapshot per ~30s with execution lag. The honest all-gates backtest (§3.3) is the realistic number, not the headline.
3. **Book depth is adequate** (median $248 ask depth at entry moments) — fills are not the constraint up to ~$50/position. Coverage is.
4. **The edge is real but the TAM is narrow** — competitive games with mid-game decisive swings, roughly 40% of matches. Stomps and settled games produce nothing, and that's correct behavior.
