#!/usr/bin/env bash
set -euo pipefail

CSI_MAC="${CSI_MAC:-88:a2:9e:5d:4e:d8}"

find_csi_iface() {
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

pkill tcpdump 2>/dev/null || true
pkill -x nexutil 2>/dev/null || true
CSI_IFACE="$(find_csi_iface || true)"
if [[ -n "$CSI_IFACE" ]]; then
  nexutil -I "$CSI_IFACE" -m0 2>/dev/null || true
  nmcli dev disconnect "$CSI_IFACE" 2>/dev/null || true
  ip link set "$CSI_IFACE" down 2>/dev/null || true
  nmcli dev set "$CSI_IFACE" managed no 2>/dev/null || true
fi
echo "CSI capture stopped"
