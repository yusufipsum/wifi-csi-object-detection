#!/usr/bin/env bash
set -euo pipefail

DEFAULT_TARGET="$(ip route | awk '/^default/ {print $3; exit}')"
TARGET="${1:-${DEFAULT_TARGET:-192.168.1.7}}"
PORT="${2:-5501}"
TX_PROFILE="${TX_PROFILE:-multiscale_physical_v1}"
RATE_PPS="${RATE_PPS:-15}"
PAYLOAD_SIZE="${PAYLOAD_SIZE:-1200}"
TARGET_CSI_PACKET_RATE="${TARGET_CSI_PACKET_RATE:-15-20}"
CSI_TX_TEE="${CSI_TX_TEE:-0}"
LOG_DIR="${LOG_DIR:-/home/admin/csi/logs}"
TS="$(date +%Y%m%d-%H%M%S)"
LOG="$LOG_DIR/start-tx-$TS.log"
mkdir -p "$LOG_DIR"
if [[ "$CSI_TX_TEE" == "1" ]]; then
  exec > >(tee -a "$LOG") 2>&1
else
  exec >>"$LOG" 2>&1
fi

echo "[$(date -Is)] csi-bravo transmitter"
echo "profile=$TX_PROFILE target=$TARGET port=$PORT rate_pps=$RATE_PPS payload_size=$PAYLOAD_SIZE target_csi_packet_rate=$TARGET_CSI_PACKET_RATE"
echo "wifi_mac=$(cat /sys/class/net/wlan0/address)"
/usr/sbin/iw dev wlan0 info || true

python3 - "$TARGET" "$PORT" "$RATE_PPS" "$PAYLOAD_SIZE" <<'PY'
import os
import socket
import sys
import time

target = sys.argv[1]
port = int(sys.argv[2])
rate = float(sys.argv[3])
size = int(sys.argv[4])
interval = 1.0 / rate if rate > 0 else 0.01
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
payload = bytearray(os.urandom(max(32, size)))
seq = 0
next_log = time.time() + 5

print("sending UDP; Ctrl-C to stop", flush=True)
while True:
    seq += 1
    payload[0:8] = seq.to_bytes(8, "big", signed=False)
    sock.sendto(payload, (target, port))
    now = time.time()
    if now >= next_log:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        print(f"sent_seq={seq} time={stamp}", flush=True)
        next_log = now + 5
    time.sleep(interval)
PY
