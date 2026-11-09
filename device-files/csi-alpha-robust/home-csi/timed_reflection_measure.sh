#!/usr/bin/env bash
set -euo pipefail

# Timed single-box reflection measurement:
#   - TX: management radio (default: AX210 / mgmt0) sends UDP packets.
#   - RX: Broadcom/Nexmon radio collects CSI from TX MAC for a fixed duration.
#   - Finish: stop capture and restore Wi-Fi only when the CSI interface was
#     carrying management connectivity before the run, or when forced.

USER_HOME="${CSI_HOME:-/home/admin}"
CSI_DIR_HOME="$USER_HOME/csi"
CAPTURE_DIR="${CAPTURE_DIR:-$CSI_DIR_HOME/captures}"
LOG_DIR="${LOG_DIR:-$CSI_DIR_HOME/logs}"

DURATION_SECS="${1:-60}"
LABEL="${2:-reflection_test}"
CHSPEC="${3:-48/80}"
SOURCE_MAC_ARG="${4:-}"
DISTANCE_M="${5:-0}"

CSI_MAC="${CSI_MAC:-88:a2:9e:5d:4e:d8}"
TX_IFACE="${TX_IFACE:-mgmt0}"
TX_TARGET="${TX_TARGET:-}"
TX_PORT="${TX_PORT:-5501}"
TX_RATE_PPS="${TX_RATE_PPS:-20}"
TX_PAYLOAD_SIZE="${TX_PAYLOAD_SIZE:-1200}"
RX_WARMUP_SECS="${RX_WARMUP_SECS:-14}"
FORCE_RESTORE_WIFI="${FORCE_RESTORE_WIFI:-0}"
WIFI_PROFILE="${WIFI_PROFILE:-netplan-wlan0-loremipsum5G}"

TS="$(date +%Y%m%d-%H%M%S)"
RUN_ID="reflection-${LABEL}-${TS}"
LOG="$LOG_DIR/$RUN_ID.log"
SESSION_META="$CAPTURE_DIR/$RUN_ID.session.json"

log() {
  printf '[%s] %s\n' "$(date -Is)" "$*"
}

die() {
  log "ERROR: $*" >&2
  exit 1
}

find_iface_by_mac() {
  local wanted="$1"
  for dev in /sys/class/net/*; do
    local name
    name="$(basename "$dev")"
    [[ "$name" == lo ]] && continue
    if [[ -f "$dev/address" && "$(cat "$dev/address")" == "$wanted" ]]; then
      echo "$name"
      return 0
    fi
  done
  return 1
}

iface_ipv4() {
  ip -4 -o addr show dev "$1" 2>/dev/null | awk '{print $4}' | head -1
}

iface_connection() {
  nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null \
    | awk -F: -v dev="$1" '$2 == dev {print $1; exit}'
}

default_gateway() {
  ip route | awk '/^default/ {print $3; exit}'
}

validate_duration() {
  [[ "$DURATION_SECS" =~ ^[0-9]+$ ]] || die "duration must be an integer number of seconds"
  (( DURATION_SECS > 0 )) || die "duration must be greater than zero"
}

write_session_meta() {
  local phase="$1"
  local latest_pcap="${2:-}"
  python3 - "$SESSION_META" "$phase" "$RUN_ID" "$LABEL" "$DURATION_SECS" \
    "$CHSPEC" "$CSI_IFACE" "$CSI_MAC" "$TX_IFACE" "$TX_MAC" "$TX_TARGET" \
    "$TX_PORT" "$TX_RATE_PPS" "$TX_PAYLOAD_SIZE" "$DISTANCE_M" \
    "$WIFI_PROFILE" "$NEEDS_WIFI_RESTORE" "$latest_pcap" <<'PY'
import json
import sys
from datetime import datetime, timezone

(
    meta_path,
    phase,
    run_id,
    label,
    duration,
    chspec,
    csi_iface,
    csi_mac,
    tx_iface,
    tx_mac,
    tx_target,
    tx_port,
    tx_rate_pps,
    tx_payload_size,
    distance_m,
    wifi_profile,
    needs_wifi_restore,
    latest_pcap,
) = sys.argv[1:]

payload = {
    "role": "timed_reflection_measurement",
    "phase": phase,
    "run_id": run_id,
    "label": label,
    "device": "csi-alpha",
    "created_or_updated_at": datetime.now(timezone.utc).isoformat(),
    "duration_secs": int(duration),
    "channel": chspec,
    "distance_m": float(distance_m) if distance_m else None,
    "rx": {
        "iface": csi_iface,
        "mac": csi_mac,
        "pipeline": "nexmon_csi",
        "source_mac_filter": tx_mac,
    },
    "tx": {
        "iface": tx_iface,
        "mac": tx_mac,
        "target": tx_target,
        "port": int(tx_port),
        "rate_pps": float(tx_rate_pps),
        "payload_size": int(tx_payload_size),
    },
    "restore": {
        "wifi_profile": wifi_profile,
        "needed": needs_wifi_restore == "1",
    },
    "artifacts": {
        "session_meta": meta_path,
        "latest_pcap": latest_pcap or None,
    },
}

with open(meta_path, "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2)
    f.write("\n")
PY
}

stop_tx() {
  if [[ -n "${TX_PID:-}" ]] && kill -0 "$TX_PID" 2>/dev/null; then
    kill "$TX_PID" 2>/dev/null || true
    wait "$TX_PID" 2>/dev/null || true
  fi
}

finish() {
  local exit_code=$?
  set +e
  log "finishing measurement exit_code=$exit_code"
  stop_tx

  if [[ "${NEEDS_WIFI_RESTORE:-0}" == "1" || "$FORCE_RESTORE_WIFI" == "1" ]]; then
    log "restoring Wi-Fi profile=$WIFI_PROFILE"
    WIFI_PROFILE="$WIFI_PROFILE" CSI_MAC="$CSI_MAC" "$CSI_DIR_HOME/restore_wifi.sh"
  else
    log "stopping CSI capture without Wi-Fi restore"
    "$CSI_DIR_HOME/stop_rx.sh"
  fi

  local latest_pcap=""
  latest_pcap="$(find "$CAPTURE_DIR" -maxdepth 1 -type f -name '*.pcap' -newer "$LOG" -print 2>/dev/null | sort | tail -1)"
  if [[ -z "$latest_pcap" ]]; then
    latest_pcap="$(ls -t "$CAPTURE_DIR"/*.pcap 2>/dev/null | head -1)"
  fi
  write_session_meta "finished" "$latest_pcap" || true
  log "session_meta=$SESSION_META"
  [[ -n "$latest_pcap" ]] && log "latest_pcap=$latest_pcap"
  exit "$exit_code"
}

validate_duration
mkdir -p "$CAPTURE_DIR" "$LOG_DIR"
touch "$LOG"
exec > >(tee -a "$LOG") 2>&1

trap finish EXIT INT TERM

[[ -x "$CSI_DIR_HOME/start_rx.sh" ]] || die "missing executable $CSI_DIR_HOME/start_rx.sh"
[[ -x "$CSI_DIR_HOME/stop_rx.sh" ]] || die "missing executable $CSI_DIR_HOME/stop_rx.sh"
[[ -x "$CSI_DIR_HOME/restore_wifi.sh" ]] || die "missing executable $CSI_DIR_HOME/restore_wifi.sh"
[[ -d "/sys/class/net/$TX_IFACE" ]] || die "TX_IFACE=$TX_IFACE does not exist"

CSI_IFACE="$(find_iface_by_mac "$CSI_MAC" || true)"
[[ -n "$CSI_IFACE" ]] || die "could not find CSI interface with MAC $CSI_MAC"

TX_MAC="${SOURCE_MAC_ARG:-$(cat "/sys/class/net/$TX_IFACE/address")}"
TX_TARGET="${TX_TARGET:-$(default_gateway)}"
[[ -n "$TX_TARGET" ]] || die "could not determine TX_TARGET; set TX_TARGET=<ip>"

CSI_ACTIVE_CONN="$(iface_connection "$CSI_IFACE" || true)"
CSI_IPV4="$(iface_ipv4 "$CSI_IFACE" || true)"
NEEDS_WIFI_RESTORE=0
if [[ -n "$CSI_ACTIVE_CONN" || -n "$CSI_IPV4" ]]; then
  NEEDS_WIFI_RESTORE=1
fi

log "timed reflection measurement"
log "run_id=$RUN_ID label=$LABEL duration=${DURATION_SECS}s channel=$CHSPEC"
log "rx_iface=$CSI_IFACE rx_mac=$CSI_MAC source_mac_filter=$TX_MAC"
log "tx_iface=$TX_IFACE tx_target=$TX_TARGET tx_port=$TX_PORT tx_rate_pps=$TX_RATE_PPS"
log "csi_active_connection=${CSI_ACTIVE_CONN:-none} csi_ipv4=${CSI_IPV4:-none} restore_needed=$NEEDS_WIFI_RESTORE"
write_session_meta "starting" ""

log "starting RX"
CSI_MAC="$CSI_MAC" "$CSI_DIR_HOME/start_rx.sh" "$CHSPEC" "$TX_MAC" "$DISTANCE_M" &
RX_PID=$!

sleep "$RX_WARMUP_SECS"
if ! kill -0 "$RX_PID" 2>/dev/null; then
  wait "$RX_PID"
fi

log "starting TX for ${DURATION_SECS}s"
python3 - "$TX_TARGET" "$TX_PORT" "$TX_RATE_PPS" "$TX_PAYLOAD_SIZE" "$TX_IFACE" <<'PY' &
import os
import socket
import subprocess
import sys
import time

target = sys.argv[1]
port = int(sys.argv[2])
rate = float(sys.argv[3])
size = int(sys.argv[4])
iface = sys.argv[5]
interval = 1.0 / rate if rate > 0 else 0.01

source_ip = ""
try:
    out = subprocess.check_output(
        ["ip", "-4", "-o", "addr", "show", "dev", iface],
        text=True,
        stderr=subprocess.DEVNULL,
    )
    fields = out.split()
    if len(fields) >= 4:
        source_ip = fields[3].split("/", 1)[0]
except Exception:
    source_ip = ""

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
if source_ip:
    sock.bind((source_ip, 0))

payload = bytearray(os.urandom(max(32, size)))
seq = 0
next_log = time.time() + 5
print(f"tx_loop target={target}:{port} iface={iface} source_ip={source_ip or 'default'} rate_pps={rate}", flush=True)

while True:
    seq += 1
    payload[0:8] = seq.to_bytes(8, "big", signed=False)
    sock.sendto(payload, (target, port))
    now = time.time()
    if now >= next_log:
        print(f"tx_sent_seq={seq} time={time.strftime('%Y-%m-%dT%H:%M:%S%z')}", flush=True)
        next_log = now + 5
    time.sleep(interval)
PY
TX_PID=$!

sleep "$DURATION_SECS"
log "duration complete"

stop_tx
if kill -0 "$RX_PID" 2>/dev/null; then
  log "stopping RX process pid=$RX_PID"
  kill "$RX_PID" 2>/dev/null || true
  wait "$RX_PID" 2>/dev/null || true
fi

log "measurement complete"
