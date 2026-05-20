#!/usr/bin/env bash
set -u

MGMT_CONN="${MGMT_CONN:-netplan-wlan0-loremipsum5G}"
FALLBACK_CONN="${FALLBACK_CONN:-csi-fallback-broadcom}"
LOG=/var/log/csi-alpha-net-guard.log

exec >>"$LOG" 2>&1
echo "[$(date -Is)] csi-alpha-net-guard starting"

systemctl start NetworkManager || true
sleep 3
nmcli radio wifi on || true

wait_for_iface() {
  local iface="$1"
  local limit="${2:-45}"
  local i
  for i in $(seq 1 "$limit"); do
    [[ -d "/sys/class/net/$iface" ]] && return 0
    sleep 1
  done
  return 1
}

has_ipv4() {
  ip -4 -o addr show dev "$1" 2>/dev/null | grep -q ' inet '
}

bring_mgmt_up() {
  echo "[$(date -Is)] trying mgmt0"
  wait_for_iface mgmt0 60 || return 1
  nmcli dev set mgmt0 managed yes || true
  ip link set mgmt0 up || true
  iw dev mgmt0 set power_save off 2>/dev/null || true
  nmcli connection up "$MGMT_CONN" ifname mgmt0 || nmcli connection up "$MGMT_CONN" || return 1

  local i
  for i in $(seq 1 45); do
    if has_ipv4 mgmt0; then
      echo "[$(date -Is)] mgmt0 has IPv4"
      ip -br addr
      nmcli dev disconnect csi0 2>/dev/null || true
      nmcli dev set csi0 managed no 2>/dev/null || true
      ip addr flush dev csi0 2>/dev/null || true
      ip link set csi0 down 2>/dev/null || true
      systemctl restart ssh avahi-daemon || true
      return 0
    fi
    sleep 1
  done
  return 1
}

bring_fallback_up() {
  echo "[$(date -Is)] mgmt0 failed; falling back to csi0 normal Wi-Fi"
  wait_for_iface csi0 30 || return 1
  nmcli dev set csi0 managed yes || true
  ip link set csi0 up || true
  iw dev csi0 set power_save off 2>/dev/null || true
  nmcli connection up "$FALLBACK_CONN" ifname csi0 || nmcli connection up "$FALLBACK_CONN" || true
  systemctl restart ssh avahi-daemon || true
  ip -br addr
}

bring_mgmt_up || bring_fallback_up
echo "[$(date -Is)] csi-alpha-net-guard done"
