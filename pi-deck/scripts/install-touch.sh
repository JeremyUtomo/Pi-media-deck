#!/bin/bash
# Installs the GT911 polling touch daemon (pi-deck-touch.service) and keeps the kernel goodix driver from
# loading. Run once: sudo bash ~/pi-deck/scripts/install-touch.sh
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
install -D -m 0755 "$here/touchd/gt911_touchd.py" /usr/local/lib/pi-deck/gt911_touchd.py
install -m 0644 "$here/systemd/pi-deck-touch.service" /etc/systemd/system/pi-deck-touch.service
printf '# Pi Media Deck: the GT911 is polled by pi-deck-touch.service; its IRQ line floats on the HyperPixel 4.\nblacklist goodix_ts\n' \
	> /etc/modprobe.d/pi-deck-no-goodix.conf

# Previous approach (GPIO 27 pull + driver rebind), superseded.
rm -f /usr/local/sbin/pi-deck-touch-fix

systemctl daemon-reload
systemctl enable pi-deck-touch.service
systemctl restart pi-deck-touch.service
sleep 3
systemctl status --no-pager --lines=6 pi-deck-touch.service
