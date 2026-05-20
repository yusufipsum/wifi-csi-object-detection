#!/usr/bin/env bash
set -euo pipefail

RUN_USER="${SUDO_USER:-${USER:-admin}}"
USER_HOME="${CSI_HOME:-$(getent passwd "$RUN_USER" | cut -d: -f6)}"
[[ -n "$USER_HOME" ]] || USER_HOME="/home/admin"
NEXMON_ROOT="${NEXMON_ROOT:-$USER_HOME/src/nexmon}"
CSI_DIR="$NEXMON_ROOT/patches/bcm43455c0/7_45_189/nexmon_csi"
WIFI_PROFILE="${WIFI_PROFILE:-netplan-wlan0-loremipsum5G}"

pkill tcpdump 2>/dev/null || true
pkill -x nexutil 2>/dev/null || true

if [[ -d "$CSI_DIR" ]]; then
  cd "$CSI_DIR"
  . "$NEXMON_ROOT/setup_env.sh"
fi

update-alternatives --auto cyfmac43455-sdio.bin || true
nmcli radio wifi off || true
modprobe -r brcmfmac_wcc brcmfmac 2>/dev/null || true
sleep 2
modprobe brcmfmac
sleep 3

nmcli radio wifi on || true
nmcli dev set wlan0 managed yes || true
ip link set wlan0 up || true
iw dev wlan0 set power_save off 2>/dev/null || true
nmcli connection up "$WIFI_PROFILE"
systemctl restart ssh avahi-daemon || true

ip -br addr
iw dev wlan0 link || true
