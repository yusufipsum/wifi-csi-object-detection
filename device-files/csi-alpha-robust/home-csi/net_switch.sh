#!/usr/bin/env bash
# csi-alpha guvenli ag degistirme yardimcisi (NetworkManager).
# Web servisinden systemd-run ile ayri birim olarak calistirilir; HTTP baglantisi kopsa da tamamlanir.
#
#   net_switch.sh connect <ssid> <parola-dosyasi|-> [hidden] [oncelik]
#   net_switch.sh activate <baglanti-adi>
#   net_switch.sh hotspot-on [ssid] [parola-dosyasi]
#   net_switch.sh hotspot-off
#
# Basarisizlikta: once onceki baglanti geri yuklenir, o da olmazsa kurulum hotspot'u acilir.
set -u

IFACE="${MGMT_IFACE:-mgmt0}"
CSI_HOME="${CSI_HOME:-/home/admin/csi}"
LOG="$CSI_HOME/logs/net-switch.log"
CONF="$CSI_HOME/config/network.json"
HOTSPOT_CON="csi-hotspot"
mkdir -p "$CSI_HOME/logs"
exec >>"$LOG" 2>&1

log() { printf '[%s] %s\n' "$(date -Is)" "$*"; }

conf_get() {
  python3 - "$CONF" "$1" "$2" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception:
    data = {}
print(data.get(sys.argv[2]) or sys.argv[3])
PY
}

has_ipv4() { ip -4 -o addr show dev "$IFACE" 2>/dev/null | grep -q ' inet '; }

wait_ipv4() {
  local i
  for i in $(seq 1 "${1:-40}"); do
    has_ipv4 && return 0
    sleep 1
  done
  return 1
}

active_con() {
  nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null | awk -F: -v d="$IFACE" '$2 == d {print $1; exit}'
}

after_up() {
  systemctl restart avahi-daemon 2>/dev/null || true
  log "OK $(active_con) ip=$(ip -4 -o addr show dev "$IFACE" | awk '{print $4}' | head -1)"
}

hotspot_on() {
  local ssid="${1:-}" pwfile="${2:-}" pw=""
  [[ -z "$ssid" ]] && ssid="$(conf_get hotspotSsid csi-alpha-kurulum)"
  if [[ -n "$pwfile" && -f "$pwfile" ]]; then pw="$(cat "$pwfile")"; rm -f "$pwfile"; fi
  [[ -z "$pw" ]] && pw="$(conf_get hotspotPassword csialpha2026)"
  local ch; ch="$(conf_get hotspotChannel 6)"
  log "hotspot on ssid=$ssid channel=$ch"
  nmcli connection delete "$HOTSPOT_CON" >/dev/null 2>&1 || true
  if nmcli device wifi hotspot ifname "$IFACE" con-name "$HOTSPOT_CON" ssid "$ssid" band bg channel "$ch" password "$pw"; then
    nmcli connection modify "$HOTSPOT_CON" connection.autoconnect no >/dev/null 2>&1 || true
    wait_ipv4 20 && after_up && return 0
  fi
  log "hotspot FAILED"
  return 1
}

restore_prev() {
  local prev="$1"
  if [[ -n "$prev" && "$prev" != "$HOTSPOT_CON" ]]; then
    log "restoring previous connection: $prev"
    nmcli connection up "$prev" ifname "$IFACE" && wait_ipv4 40 && after_up && return 0
  fi
  log "trying any saved connection"
  nmcli device connect "$IFACE" && wait_ipv4 40 && after_up && return 0
  hotspot_on
}

cmd="${1:-}"
shift || true
prev="$(active_con)"
log "cmd=$cmd prev=${prev:-none}"

case "$cmd" in
  connect)
    ssid="${1:?ssid}"; pwfile="${2:--}"; hidden="${3:-no}"; prio="${4:-50}"
    pw=""
    if [[ "$pwfile" != "-" && -f "$pwfile" ]]; then pw="$(cat "$pwfile")"; rm -f "$pwfile"; fi
    name="csi-wifi-$(printf '%s' "$ssid" | tr -c 'A-Za-z0-9_-' '_' | cut -c1-40)"
    [[ "$prev" == "$HOTSPOT_CON" ]] && nmcli connection down "$HOTSPOT_CON" >/dev/null 2>&1
    nmcli connection delete "$name" >/dev/null 2>&1 || true
    nmcli device wifi rescan ifname "$IFACE" >/dev/null 2>&1 || true
    sleep 3
    args=(device wifi connect "$ssid" ifname "$IFACE" name "$name")
    [[ -n "$pw" ]] && args+=(password "$pw")
    [[ "$hidden" == "yes" ]] && args+=(hidden yes)
    log "connecting ssid=$ssid name=$name hidden=$hidden"
    if timeout 60 nmcli "${args[@]}" && wait_ipv4 40; then
      nmcli connection modify "$name" connection.autoconnect yes connection.autoconnect-priority "$prio" || true
      after_up
      exit 0
    fi
    log "connect FAILED ssid=$ssid"
    nmcli connection delete "$name" >/dev/null 2>&1 || true
    restore_prev "$prev"
    ;;
  activate)
    name="${1:?baglanti adi}"
    [[ "$prev" == "$HOTSPOT_CON" ]] && nmcli connection down "$HOTSPOT_CON" >/dev/null 2>&1
    if nmcli connection up "$name" ifname "$IFACE" && wait_ipv4 40; then after_up; exit 0; fi
    log "activate FAILED $name"
    restore_prev "$prev"
    ;;
  hotspot-on)
    hotspot_on "${1:-}" "${2:-}" || restore_prev "$prev"
    ;;
  hotspot-off)
    nmcli connection down "$HOTSPOT_CON" >/dev/null 2>&1 || true
    nmcli device wifi rescan ifname "$IFACE" >/dev/null 2>&1 || true
    sleep 3
    nmcli device connect "$IFACE" && wait_ipv4 40 && after_up && exit 0
    log "no saved network reachable; back to hotspot"
    hotspot_on
    ;;
  *)
    log "unknown command: $cmd"
    exit 2
    ;;
esac
