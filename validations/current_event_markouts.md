# Current Event Markouts

Generated: 2026-05-25T22:05:56+00:00
Since: 2026-05-19T00:00:00+00:00

Signal markout rows: 312
Live attempt rows: 48

## Signal Markouts By Event

| event_type | n | n_m30 | avg_m10 | avg_m30 | median_m30 | win_rate_m30 | skipped | top_skip |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| POLL_RAPID_STOMP | 133 | 129 | -0.0334 | -0.0228 | -0.0050 | 0.33 | 115 | chasing_terminal_price |
| POLL_DECISIVE_STOMP | 147 | 143 | -0.0261 | -0.0258 | -0.0150 | 0.19 | 141 | edge_too_small |
| POLL_LEAD_FLIP_WITH_KILLS | 1 | 1 | -0.0200 | -0.0350 | -0.0350 | 0.00 | 0 | event_lag_signal |
| POLL_COMEBACK_RECOVERY | 10 | 6 | -0.0283 | -0.0400 | -0.0175 | 0.33 | 10 | edge_too_small |
| POLL_MAJOR_COMEBACK_RECOVERY | 18 | 18 | -0.0537 | -0.0504 | -0.0200 | 0.28 | 18 | fill_price_too_low |
| POLL_FIGHT_SWING | 1 | 1 | -0.1800 | -0.1000 | -0.1000 | 0.00 | 1 | fill_price_too_high |
| POLL_KILL_BURST_CONFIRMED | 2 | 2 | -0.1500 | -0.1375 | -0.1375 | 0.00 | 2 | volatility_spread_too_wide |

## Live Attempts By Event

| event_type | attempts | submitted_usd | filled_usd | statuses |
|---|---:|---:|---:|---|
| POLL_RAPID_STOMP | 17 | 70.00 | 0.00 | delayed:12, rejected_precheck:3, exception:2 |
| POLL_DECISIVE_STOMP | 6 | 10.00 | 0.00 | rejected_precheck:4, delayed:2 |
| POLL_LEAD_FLIP_WITH_KILLS | 1 | 0.00 | 0.00 | rejected_precheck:1 |

## Cap Diagnostics By Reference Ask

| event_type | current_cap | ask_bucket | n | avg_m30 | win_rate_m30 |
|---|---:|---|---:|---:|---:|
| POLL_COMEBACK_RECOVERY | 0.85 | 0.65-0.80 | 1 | -0.0700 | 0.00 |
| POLL_COMEBACK_RECOVERY | 0.85 | <0.65 | 4 | 0.0075 | 0.50 |
| POLL_COMEBACK_RECOVERY | 0.85 | >=0.88 | 1 | -0.2000 | 0.00 |
| POLL_DECISIVE_STOMP | 0.88 | 0.65-0.80 | 11 | 0.0136 | 0.45 |
| POLL_DECISIVE_STOMP | 0.88 | 0.80-0.88 | 17 | 0.0025 | 0.47 |
| POLL_DECISIVE_STOMP | 0.88 | <0.65 | 12 | -0.0804 | 0.00 |
| POLL_DECISIVE_STOMP | 0.88 | >=0.88 | 103 | -0.0283 | 0.14 |
| POLL_FIGHT_SWING | 0.82 | >=0.88 | 1 | -0.1000 | 0.00 |
| POLL_KILL_BURST_CONFIRMED | 0.84 | 0.65-0.80 | 1 | -0.0800 | 0.00 |
| POLL_KILL_BURST_CONFIRMED | 0.84 | <0.65 | 1 | -0.1950 | 0.00 |
| POLL_LEAD_FLIP_WITH_KILLS | 0.84 | <0.65 | 1 | -0.0350 | 0.00 |
| POLL_MAJOR_COMEBACK_RECOVERY | 0.87 | 0.65-0.80 | 3 | -0.0985 | 0.00 |
| POLL_MAJOR_COMEBACK_RECOVERY | 0.87 | <0.65 | 14 | -0.0423 | 0.36 |
| POLL_MAJOR_COMEBACK_RECOVERY | 0.87 | >=0.88 | 1 | -0.0200 | 0.00 |
| POLL_RAPID_STOMP | 0.80 | 0.65-0.80 | 14 | 0.0121 | 0.57 |
| POLL_RAPID_STOMP | 0.80 | 0.80-0.88 | 18 | -0.0381 | 0.28 |
| POLL_RAPID_STOMP | 0.80 | <0.65 | 18 | 0.0183 | 0.39 |
| POLL_RAPID_STOMP | 0.80 | >=0.88 | 79 | -0.0348 | 0.28 |
