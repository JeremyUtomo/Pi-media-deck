#!/bin/bash
# Turns on USB networking to the PC (one cable for power and data), using Raspberry Pi OS's rpi-usb-gadget.
# Run once on the Pi:  sudo bash ~/pi-deck/scripts/install-usb.sh   then reboot.
#
# rpi-usb-gadget puts the USB port in device mode (dwc2 peripheral + g_ether) and makes usb0 a NetworkManager
# "shared" connection: the Pi is 10.12.194.1/28 and hands the PC an address by DHCP. The deck finds the PC on
# that link itself (endpoint "ws://usb:8765"). Undo with: sudo rpi-usb-gadget off
set -eu

rpi-usb-gadget on

# The link is only for the deck: don't offer the Pi as the PC's default gateway or DNS server, or Windows may
# send its internet traffic through the Pi's Wi-Fi.
mkdir -p /etc/NetworkManager/dnsmasq-shared.d
cat > /etc/NetworkManager/dnsmasq-shared.d/91-pi-deck-no-gateway.conf <<'EOF'
# Pi Media Deck: USB link carries only deck traffic; no default route or DNS for the PC.
dhcp-option=3
dhcp-option=6
EOF

echo
echo "USB gadget mode is on. Reboot to switch the USB port to device mode:  sudo reboot"
