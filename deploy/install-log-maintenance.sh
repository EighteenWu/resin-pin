#!/bin/bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "run as root" >&2
    exit 1
fi
SOURCE=$(cd -- "$(dirname -- "$0")" && pwd)
BACKUP="/root/pan-so-runtime-backups/log-maintenance-$(date +%Y%m%d-%H%M%S)"
install -d -m 0700 "$BACKUP"
for path in /etc/logrotate.d/btmp /etc/systemd/system/panso-log-maintenance.service /etc/systemd/system/panso-log-maintenance.timer; do
    if [ -e "$path" ]; then
        cp -p -- "$path" "$BACKUP/$(basename -- "$path")"
    fi
done
install -m 0644 "$SOURCE/btmp.logrotate" /etc/logrotate.d/btmp
install -m 0644 "$SOURCE/panso-log-maintenance.service" /etc/systemd/system/panso-log-maintenance.service
install -m 0644 "$SOURCE/panso-log-maintenance.timer" /etc/systemd/system/panso-log-maintenance.timer
systemd-analyze verify /etc/systemd/system/panso-log-maintenance.service /etc/systemd/system/panso-log-maintenance.timer
systemctl daemon-reload
TZ=Asia/Shanghai /usr/bin/python3 "$SOURCE/host_log_maintenance.py" --apply --force-rotate
systemctl enable --now panso-log-maintenance.timer
echo "configuration backup: $BACKUP"
