#!/usr/bin/env bash
set -u

WIFI_PROFILE="${WIFI_PROFILE:-csi-fallback-broadcom}"
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
update-alternatives --auto cyfmac43455-sdio.bin || true
systemctl restart NetworkManager || true
sleep 3
modprobe -r brcmfmac_wcc brcmfmac 2>/dev/null || true
sleep 2
modprobe brcmfmac
sleep 5
systemctl restart NetworkManager || true
sleep 5

CSI_IFACE="$(find_csi_iface || echo csi0)"
rfkill unblock all 2>/dev/null || true
nmcli radio wifi on 2>/dev/null || true
nmcli dev set "$CSI_IFACE" managed yes 2>/dev/null || true
ip link set "$CSI_IFACE" up 2>/dev/null || true
iw dev "$CSI_IFACE" set power_save off 2>/dev/null || true

for i in 1 2 3 4 5; do
  echo "restore attempt $i on $CSI_IFACE"
  nmcli connection up "$WIFI_PROFILE" ifname "$CSI_IFACE" && break
  sleep 5
done

systemctl restart ssh avahi-daemon || true
ip -br addr
nmcli dev status || true
