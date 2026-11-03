#!/bin/bash
# Pi Media Deck Wi-Fi watchdog, run every minute by pi-deck-wifi-watchdog.timer (as root).
# If the router doesn't answer, reconnect wlan0; after 3 failed checks in a row, reload the Wi-Fi driver too.
# Never reboots. Every action is logged: journalctl -u pi-deck-wifi-watchdog
set -u

state=/run/pi-deck-wifi-watchdog.failures
gateway=$(ip -4 route show default dev wlan0 2>/dev/null | awk '{print $3; exit}')

if [ -n "$gateway" ] && ping -c 3 -W 2 -I wlan0 "$gateway" > /dev/null 2>&1; then
	if [ -s "$state" ]; then
		echo "Wi-Fi is back (gateway $gateway answers) after $(cat "$state") failed check(s)"
	fi
	rm -f "$state"
	exit 0
fi

failures=$(( $(cat "$state" 2>/dev/null || echo 0) + 1 ))
echo "$failures" > "$state"
echo "Wi-Fi check $failures failed (gateway: ${gateway:-none}); $(nmcli -t -f DEVICE,STATE device status | grep '^wlan0:')"

if [ $(( failures % 3 )) -eq 0 ]; then
	echo "Reloading the Wi-Fi driver (brcmfmac)"
	modprobe -r brcmfmac && sleep 2 && modprobe brcmfmac
	sleep 5
	/usr/sbin/iw dev wlan0 set power_save off 2>/dev/null || true
fi
echo "Reconnecting wlan0"
nmcli device reconnect wlan0 || nmcli device connect wlan0 || true
