#!/bin/bash
# Install scripts/dota-poly-bot.service as a user-managed systemd unit.
#
# Requires sudo. Inspect the service file at scripts/dota-poly-bot.service
# before running this.
#
# WSL2 note: systemd must be enabled in /etc/wsl.conf:
#   [boot]
#   systemd=true
# After editing wsl.conf, run `wsl --shutdown` from PowerShell then reopen WSL.
#
# Usage:
#   bash scripts/install_systemd.sh           # install + enable + start
#   bash scripts/install_systemd.sh status    # show status
#   bash scripts/install_systemd.sh logs      # tail journal
#   bash scripts/install_systemd.sh stop      # stop service
#   bash scripts/install_systemd.sh disable   # stop + disable (keep file)
#   bash scripts/install_systemd.sh uninstall # stop + disable + remove file

set -euo pipefail

SERVICE_NAME="dota-poly-bot.service"
SERVICE_SRC="$(dirname "$(readlink -f "$0")")/dota-poly-bot.service"
SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}"

cmd="${1:-install}"

case "$cmd" in
  install)
    if ! command -v systemctl >/dev/null 2>&1; then
      echo "systemctl not found. On WSL2 enable systemd via /etc/wsl.conf first." >&2
      exit 1
    fi
    if [[ ! -f "$SERVICE_SRC" ]]; then
      echo "missing $SERVICE_SRC" >&2
      exit 1
    fi
    echo "Installing $SERVICE_DST (requires sudo)..."
    sudo install -m 644 "$SERVICE_SRC" "$SERVICE_DST"
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"
    sudo systemctl start "$SERVICE_NAME"
    echo "Installed and started. Check with: bash $0 status"
    ;;
  status)
    systemctl status "$SERVICE_NAME" --no-pager
    ;;
  logs)
    journalctl -u "$SERVICE_NAME" -f --no-pager
    ;;
  stop)
    sudo systemctl stop "$SERVICE_NAME"
    ;;
  disable)
    sudo systemctl stop "$SERVICE_NAME" || true
    sudo systemctl disable "$SERVICE_NAME"
    ;;
  uninstall)
    sudo systemctl stop "$SERVICE_NAME" || true
    sudo systemctl disable "$SERVICE_NAME" || true
    sudo rm -f "$SERVICE_DST"
    sudo systemctl daemon-reload
    echo "Removed $SERVICE_DST"
    ;;
  *)
    echo "usage: $0 [install|status|logs|stop|disable|uninstall]" >&2
    exit 1
    ;;
esac
