#!/bin/bash
# Installs and starts the deck as a systemd user service (no sudo). Run on the Pi as the desktop user:
#   bash ~/pi-deck/scripts/install-autostart.sh
# Afterwards: systemctl --user restart pi-deck (after deploying), journalctl --user -u pi-deck -f (logs).
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p ~/.config/systemd/user ~/.config/pi-deck
install -m 0644 "$here/systemd/pi-deck.service" ~/.config/systemd/user/pi-deck.service
[ -f ~/.config/pi-deck/config.toml ] || cp "$here/config.example.toml" ~/.config/pi-deck/config.toml

pkill -f "python -m deck" || true  # a copy started by hand
systemctl --user daemon-reload
systemctl --user enable pi-deck.service
systemctl --user restart pi-deck.service
sleep 5
systemctl --user --no-pager --lines=5 status pi-deck.service
