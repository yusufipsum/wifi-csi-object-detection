#!/usr/bin/env bash
# csi-alpha acilis ag korumasi.
# Sira: 1) mgmt0 (AX210) kayitli herhangi bir Wi-Fi (NetworkManager onceligiyle)
#       2) eth0 kablo
#       3) mgmt0 uzerinde kurulum hotspot'u (2.4 GHz) -> panel http://10.42.0.1:8080
#       4) son care: Broadcom csi0 normal Wi-Fi (eski davranis)
# Belirli bir ag adina bagli degildir; yeni aglar panelden (Ag sekmesi) eklenir.
set -u

CSI_HOME="${CSI_HOME:-/home/admin/csi}"
FALLBACK_CONN="${FALLBACK_CONN:-csi-fallback-broadcom}"
WAIT_WIFI_S="${WAIT_WIFI_S:-45}"
LOG=/var/log/csi-alpha-net-guard.log

exec >>"$LOG" 2>&1
echo "[$(date -Is)] csi-alpha-net-guard starting"

systemctl start NetworkManager || true
sleep 3
nmcli radio wifi on || true

wait_for_iface() {
  local iface="$1" limit="${2:-45}" i
  for i in $(seq 1 "$limit"); do
    [[ -d "/sys/class/net/$iface" ]] && return 0
    sleep 1
  done
  return 1
}

has_ipv4() {
  ip -4 -o addr show dev "$1" 2>/dev/null | grep -q ' inet '
}

park_csi0() {
  nmcli dev disconnect csi0 2>/dev/null || true
  nmcli dev set csi0 managed no 2>/dev/null || true
  ip addr flush dev csi0 2>/dev/null || true
  ip link set csi0 down 2>/dev/null || true
}

bring_mgmt_up() {
  echo "[$(date -Is)] mgmt0: waiting for any saved Wi-Fi (autoconnect)"
  wait_for_iface mgmt0 60 || return 1
  nmcli dev set mgmt0 managed yes || true
  ip link set mgmt0 up || true
  iw dev mgmt0 set power_save off 2>/dev/null || /usr/sbin/iw dev mgmt0 set power_save off 2>/dev/null || true
  nmcli dev wifi rescan ifname mgmt0 2>/dev/null || true
  nmcli dev connect mgmt0 2>/dev/null || true
  local i
  for i in $(seq 1 "$WAIT_WIFI_S"); do
    if has_ipv4 mgmt0; then
      echo "[$(date -Is)] mgmt0 has IPv4 ($(nmcli -t -f NAME,DEVICE con show --active | grep ':mgmt0' | cut -d: -f1))"
      return 0
    fi
    sleep 1
  done
  return 1
}

bring_eth_up() {
  [[ -d /sys/class/net/eth0 ]] || return 1
  nmcli dev connect eth0 2>/dev/null || true
  local i
  for i in $(seq 1 10); do
    has_ipv4 eth0 && { echo "[$(date -Is)] eth0 has IPv4"; return 0; }
    sleep 1
  done
  return 1
}

bring_hotspot_up() {
  echo "[$(date -Is)] no network; starting setup hotspot on mgmt0"
  [[ -x "$CSI_HOME/net_switch.sh" ]] || return 1
  "$CSI_HOME/net_switch.sh" hotspot-on
  has_ipv4 mgmt0
}

bring_fallback_up() {
  echo "[$(date -Is)] hotspot failed; falling back to csi0 normal Wi-Fi"
  wait_for_iface csi0 30 || return 1
  nmcli dev set csi0 managed yes || true
  ip link set csi0 up || true
  nmcli connection up "$FALLBACK_CONN" ifname csi0 || true
}

if bring_mgmt_up; then
  park_csi0
elif bring_eth_up; then
  park_csi0
elif bring_hotspot_up; then
  park_csi0
else
  bring_fallback_up
fi
systemctl restart ssh avahi-daemon || true
ip -br addr
echo "[$(date -Is)] csi-alpha-net-guard done"
