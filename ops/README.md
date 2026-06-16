# Operations

## systemd Service

Install the live bot service:

```bash
sudo cp ops/dota-poly-live.service /etc/systemd/system/dota-poly-live.service
sudo systemctl daemon-reload
sudo systemctl enable dota-poly-live.service
sudo systemctl start dota-poly-live.service
```

Inspect status/logs:

```bash
systemctl status dota-poly-live.service
journalctl -u dota-poly-live.service -f
```

The unit uses `Restart=on-failure` and `RestartSec=10`. On every process start,
`main.py` cancels stale CLOB orders and runs startup reconciliation before
normal live trading resumes.

Python now writes rotating application logs to `logs/bot.log`; do not launch
the service with shell redirection to that file.

## Telegram Alerts

Set these environment variables in `.env` or the systemd environment:

```bash
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Liveness alert, intended to run hourly during known DreamLeague windows:

```bash
DREAMLEAGUE_ACTIVE=true python3 scripts/telegram_ops.py liveness
```

Daily summary at 09:00 UTC:

```bash
python3 scripts/telegram_ops.py daily --hours 24
```

Example cron entries:

```cron
0 * * * * cd /home/tstuv/dota-poly-signal-pnl-asd && DREAMLEAGUE_ACTIVE=true /usr/bin/python3 scripts/telegram_ops.py liveness
0 9 * * * cd /home/tstuv/dota-poly-signal-pnl-asd && /usr/bin/python3 scripts/telegram_ops.py daily --hours 24
```

## Disk Guard

The bot halts new live orders when free disk space drops below the threshold,
but continues read-only monitoring/logging where possible.

Environment knobs:

```bash
DISK_GUARD_PATH=/
DISK_GUARD_MIN_FREE_GB=2
DISK_GUARD_CHECK_INTERVAL_SEC=3600
```

Low-disk live attempts are rejected with:

```text
disk_guard_low_free_space:free_gb=X.XX_min_gb=Y.YY
```
