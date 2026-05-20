#!/usr/bin/env bash
set -euo pipefail

CSI_MAC="${CSI_MAC:-88:a2:9e:5d:4e:d8}"
CHSPEC="${1:-48/80}"
SOURCE_MAC="${2:-}"
DISTANCE_M="${3:-}"
COREMASK="${COREMASK:-1}"
NSSMASK="${NSSMASK:-1}"
USER_HOME="${CSI_HOME:-/home/admin}"
CAPTURE_DIR="${CAPTURE_DIR:-$USER_HOME/csi/captures}"
NEXMON_ROOT="${NEXMON_ROOT:-$USER_HOME/src/nexmon}"
CSI_DIR="$NEXMON_ROOT/patches/bcm43455c0/7_45_189/nexmon_csi"
MAKECSIPARAMS="$CSI_DIR/utils/makecsiparams/makecsiparams"
TS="$(date +%Y%m%d-%H%M%S)"
BASENAME="csi-alpha-$TS"
PCAP="$CAPTURE_DIR/$BASENAME.pcap"
META="$CAPTURE_DIR/$BASENAME.json"
LOG="$USER_HOME/csi/logs/start-rx-$TS.log"

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

mkdir -p "$CAPTURE_DIR" "$USER_HOME/csi/logs"
exec > >(tee -a "$LOG") 2>&1

echo "[$(date -Is)] starting CSI RX"
echo "channel=$CHSPEC coremask=$COREMASK nssmask=$NSSMASK source_mac=${SOURCE_MAC:-none} distance_m=${DISTANCE_M:-unset} pcap=$PCAP"

python3 - "$META" "$PCAP" "$CHSPEC" "$SOURCE_MAC" "$DISTANCE_M" "$COREMASK" "$NSSMASK" <<'PY'
import json
import sys
from datetime import datetime, timezone

meta_path, pcap, channel, source_mac, distance_m, coremask, nssmask = sys.argv[1:]
payload = {
    "role": "receiver",
    "device": "csi-alpha",
    "peer": "csi-bravo",
    "peer_mac": source_mac or None,
    "distance_m": float(distance_m) if distance_m else None,
    "channel": channel,
    "coremask": coremask,
    "nssmask": nssmask,
    "pcap": pcap,
    "created_at": datetime.now(timezone.utc).isoformat(),
}
with open(meta_path, "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2)
    f.write("\n")
PY

cd "$CSI_DIR"
. "$NEXMON_ROOT/setup_env.sh"

CSI_IFACE="$(find_csi_iface)"
echo "csi_iface=$CSI_IFACE csi_mac=$CSI_MAC"

nmcli dev disconnect "$CSI_IFACE" 2>/dev/null || true
nmcli dev set "$CSI_IFACE" managed no 2>/dev/null || true
ip addr flush dev "$CSI_IFACE" 2>/dev/null || true
ip link set "$CSI_IFACE" down 2>/dev/null || true

update-alternatives --set cyfmac43455-sdio.bin /lib/firmware/nexmon/brcmfmac43455-sdio.bin
modprobe -r brcmfmac_wcc brcmfmac 2>/dev/null || true
sleep 2
modprobe brcmfmac_wcc || modprobe brcmfmac
sleep 3

CSI_IFACE="$(find_csi_iface)"
nmcli dev disconnect "$CSI_IFACE" 2>/dev/null || true
nmcli dev set "$CSI_IFACE" managed no 2>/dev/null || true
ip addr flush dev "$CSI_IFACE" 2>/dev/null || true
ip link set "$CSI_IFACE" up
iw dev "$CSI_IFACE" set power_save off 2>/dev/null || true
nexutil -I "$CSI_IFACE" -s86 -i -v0 || true

PARAM_ARGS=(-c "$CHSPEC" -C "$COREMASK" -N "$NSSMASK")
if [[ -n "$SOURCE_MAC" ]]; then
  PARAM_ARGS+=(-m "$SOURCE_MAC")
fi
PARAMS="$($MAKECSIPARAMS "${PARAM_ARGS[@]}")"
echo "nexutil params=$PARAMS"
if [[ -z "$PARAMS" ]]; then
  echo "makecsiparams returned an empty parameter string" >&2
  exit 1
fi

timeout 10 nexutil -I "$CSI_IFACE" -s500 -b -l34 -v"$PARAMS"
nexutil -I "$CSI_IFACE" -m1
echo "monitor_mode=$(nexutil -I "$CSI_IFACE" -m || true)"
echo "chanspec=$(nexutil -I "$CSI_IFACE" -k || true)"
echo "metadata=$META"
echo "[$(date -Is)] capturing CSI UDP packets on $CSI_IFACE dst port 5500"
exec tcpdump -i "$CSI_IFACE" -s 0 -U -w "$PCAP" udp dst port 5500
