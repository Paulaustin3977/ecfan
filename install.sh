#!/usr/bin/env bash
#
# Install ecfan: the daemon, its config, and a systemd unit.
#
# Usage:
#   sudo ./install.sh              install and enable the service
#   sudo ./install.sh --uninstall  stop, disable, and remove everything
#
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-/usr/local/sbin}"
CONFDEST="${CONFDEST:-/etc/ecfan.conf}"
UNITDEST="${UNITDEST:-/etc/systemd/system/ecfan.service}"
SERVICE="${SERVICE:-ecfan.service}"

die() { echo "error: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "must run as root (try: sudo ./install.sh)"

if [ -e /dev/cros_ec ]; then
    echo "found /dev/cros_ec (ChromeOS EC present)"
else
    echo "warning: /dev/cros_ec not present." >&2
    echo "         This tool only works on Chromebooks with a ChromeOS EC." >&2
    echo "         The service will not start until the device shows up." >&2
fi

case "${1:-install}" in
--uninstall|uninstall)
    echo "stopping $SERVICE"
    systemctl stop "$SERVICE" 2>/dev/null || true
    systemctl disable "$SERVICE" 2>/dev/null || true
    rm -f "$UNITDEST" "$PREFIX/ecfan" "$CONFDEST"
    systemctl daemon-reload
    echo "removed. The fan is back under EC automatic control."
    exit 0
    ;;
install|"")
    ;;
*)
    die "unknown option: $1 (expected --uninstall)"
    ;;
esac

# Keep an existing config rather than clobbering a tuned curve.
if [ -f "$CONFDEST" ]; then
    echo "keeping existing config at $CONFDEST"
    install -m 0644 "$SRC_DIR/ecfan.conf" "$CONFDEST.example"
else
    echo "installing config to $CONFDEST"
    install -m 0644 "$SRC_DIR/ecfan.conf" "$CONFDEST"
fi

echo "installing daemon to $PREFIX/ecfan"
install -m 0755 "$SRC_DIR/ecfan" "$PREFIX/ecfan"

echo "installing unit to $UNITDEST"
install -m 0644 "$SRC_DIR/ecfan.service" "$UNITDEST"

systemctl daemon-reload
systemctl enable "$SERVICE"
# Restart rather than "enable --now": if the service is already running,
# --now would leave the old process (and old binary) in place.
systemctl restart "$SERVICE"

sleep 2
systemctl --no-pager --lines=5 status "$SERVICE" || true
echo
echo "Installed. Check state with:  sudo $PREFIX/ecfan --status"
echo "Tune the curve in:           $CONFDEST"
