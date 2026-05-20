#!/usr/bin/env bash
set -euo pipefail

CSI_MAC="${CSI_MAC:-88:a2:9e:5d:4e:d8}"
CHSPEC="${1:-48/80}"
SOURCE_MAC="${2:-88:a2:9e:5d:4e:a6}"
DISTANCE_M="${3:-2}"
COREMASK="${COREMASK:-1}"
NSSMASK="${NSSMASK:-1}"
USER_HOME="${CSI_HOME:-/home/admin}"
NEXMON_ROOT="${NEXMON_ROOT:-$USER_HOME/src/nexmon}"
CSI_DIR="$NEXMON_ROOT/patches/bcm43455c0/7_45_189/nexmon_csi"
MAKECSIPARAMS="$CSI_DIR/utils/makecsiparams/makecsiparams"

log() {
  printf '[%s] %s\n' "$(date -Is)" "$*" >&2
}

find_csi_iface() {
  if [[ -n "${CSI_IFACE:-}" && -d "/sys/class/net/$CSI_IFACE" ]]; then
    echo "$CSI_IFACE"
    return 0
  fi
  for dev in /sys/class/net/*; do
    local name
    name="$(basename "$dev")"
    [[ "$name" == lo || "$name" == eth* ]] && continue
    if [[ -f "$dev/address" && "$(cat "$dev/address")" == "$CSI_MAC" ]]; then
      echo "$name"
      return 0
    fi
  done
  return 1
}

reload_csi_driver() {
  update-alternatives --set cyfmac43455-sdio.bin /lib/firmware/nexmon/brcmfmac43455-sdio.bin >&2
  modprobe -r brcmfmac_wcc brcmfmac >/dev/null 2>&1 || true
  sleep 2
  modprobe brcmfmac_wcc >/dev/null 2>&1 || modprobe brcmfmac >/dev/null 2>&1
  sleep 3
}

log "starting CSI stream channel=$CHSPEC source_mac=$SOURCE_MAC distance_m=$DISTANCE_M"

cd "$CSI_DIR"
. "$NEXMON_ROOT/setup_env.sh"

CSI_IFACE="$(find_csi_iface || true)"
if [[ -n "$CSI_IFACE" ]]; then
  log "csi_iface=$CSI_IFACE csi_mac=$CSI_MAC"
  nmcli dev disconnect "$CSI_IFACE" >/dev/null 2>&1 || true
  nmcli dev set "$CSI_IFACE" managed no >/dev/null 2>&1 || true
  ip addr flush dev "$CSI_IFACE" >/dev/null 2>&1 || true
  ip link set "$CSI_IFACE" down >/dev/null 2>&1 || true
else
  log "csi interface missing; reloading Broadcom CSI driver"
fi

reload_csi_driver

CSI_IFACE="$(find_csi_iface || true)"
if [[ -z "$CSI_IFACE" ]]; then
  log "unable to find CSI interface with mac $CSI_MAC after driver reload"
  exit 1
fi
log "csi_iface=$CSI_IFACE csi_mac=$CSI_MAC"
nmcli dev disconnect "$CSI_IFACE" >/dev/null 2>&1 || true
nmcli dev set "$CSI_IFACE" managed no >/dev/null 2>&1 || true
ip addr flush dev "$CSI_IFACE" >/dev/null 2>&1 || true
ip link set "$CSI_IFACE" up
IW_BIN="$(command -v iw || true)"
if [[ -n "$IW_BIN" ]]; then
  "$IW_BIN" dev "$CSI_IFACE" set power_save off >/dev/null 2>&1 || true
fi
nexutil -I "$CSI_IFACE" -s86 -i -v0 >&2 || true

PARAM_ARGS=(-c "$CHSPEC" -C "$COREMASK" -N "$NSSMASK")
if [[ -n "$SOURCE_MAC" ]]; then
  PARAM_ARGS+=(-m "$SOURCE_MAC")
fi
PARAMS="$($MAKECSIPARAMS "${PARAM_ARGS[@]}")"
if [[ -z "$PARAMS" ]]; then
  log "makecsiparams returned an empty parameter string"
  exit 1
fi
log "nexutil params=$PARAMS"

timeout 10 nexutil -I "$CSI_IFACE" -s500 -b -l34 -v"$PARAMS" >&2
nexutil -I "$CSI_IFACE" -m1 >&2
log "monitor_mode=$(nexutil -I "$CSI_IFACE" -m 2>/dev/null || true)"
log "chanspec=$(nexutil -I "$CSI_IFACE" -k 2>/dev/null || true)"
log "streaming tcpdump pcap from $CSI_IFACE"

exec tcpdump -i "$CSI_IFACE" -s 0 -U -w - udp dst port 5500
