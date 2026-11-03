#!/bin/bash
# Installs the Wi-Fi watchdog (pi-deck-wifi-watchdog.timer) and makes the system journal persistent, so a
# Wi-Fi dropout can be diagnosed after a power cycle. Run once: sudo bash ~/pi-deck/scripts/install-wifi-watchdog.sh
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
install -D -m 0755 "$here/scripts/wifi-watchdog.sh" /usr/local/lib/pi-deck/wifi-watchdog.sh
install -m 0644 "$here/systemd/pi-deck-wifi-watchdog.service" /etc/systemd/system/pi-deck-wifi-watchdog.service
install -m 0644 "$here/systemd/pi-deck-wifi-watchdog.timer" /etc/systemd/system/pi-deck-wifi-watchdog.timer

# Keep logs across reboots, capped so the SD card isn't filled.
mkdir -p /etc/systemd/journald.conf.d /var/log/journal
printf '# Pi Media Deck: keep logs across reboots (to diagnose Wi-Fi dropouts).\n[Journal]\nStorage=persistent\nSystemMaxUse=64M\n' \
	> /etc/systemd/journald.conf.d/pi-deck-persistent.conf
systemd-tmpfiles --create --prefix /var/log/journal
systemctl restart systemd-journald
journalctl --flush

systemctl daemon-reload
systemctl enable --now pi-deck-wifi-watchdog.timer
systemctl start pi-deck-wifi-watchdog.service
systemctl list-timers --no-pager pi-deck-wifi-watchdog.timer
journalctl --list-boots --no-pager | tail -2
