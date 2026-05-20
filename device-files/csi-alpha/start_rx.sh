#!/usr/bin/env bash
set -euo pipefail

RUN_USER="${SUDO_USER:-${USER:-admin}}"
USER_HOME="${CSI_HOME:-$(getent passwd "$RUN_USER" | cut -d: -f6)}"
[[ -n "$USER_HOME" ]] || USER_HOME="/home/admin"

CHSPEC="${1:-48/80}"
SOURCE_MAC="${2:-}"
DISTANCE_M="${3:-}"
COREMASK="${COREMASK:-1}"
NSSMASK="${NSSMASK:-1}"
CAPTURE_DIR="${CAPTURE_DIR:-$USER_HOME/csi/captures}"
NEXMON_ROOT="${NEXMON_ROOT:-$USER_HOME/src/nexmon}"
CSI_DIR="$NEXMON_ROOT/patches/bcm43455c0/7_45_189/nexmon_csi"
MAKECSIPARAMS="$CSI_DIR/utils/makecsiparams/makecsiparams"
TS="$(date +%Y%m%d-%H%M%S)"
BASENAME="csi-alpha-$TS"
PCAP="$CAPTURE_DIR/$BASENAME.pcap"
META="$CAPTURE_DIR/$BASENAME.json"
LOG="$USER_HOME/csi/logs/start-rx-$TS.log"

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

if [[ -f /lib/firmware/nexmon/brcmfmac43455-sdio.bin ]]; then
  update-alternatives --set cyfmac43455-sdio.bin /lib/firmware/nexmon/brcmfmac43455-sdio.bin
fi

make -f Makefile.rpi unmanage
make -f Makefile.rpi reload-full
ip link set wlan0 up
iw dev wlan0 set power_save off 2>/dev/null || true

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

timeout 10 nexutil -s500 -b -l34 -v"$PARAMS"
nexutil -m1

echo "monitor_mode=$(nexutil -m || true)"
echo "chanspec=$(nexutil -k || true)"
echo "metadata=$META"
echo "[$(date -Is)] capturing CSI UDP packets on wlan0 dst port 5500"
exec tcpdump -i wlan0 -s 0 -U -w "$PCAP" udp dst port 5500
