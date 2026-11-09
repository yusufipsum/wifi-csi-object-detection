#!/usr/bin/env bash
# Mac'ten csi-alpha'ya web panel + tek kutu algilama motorunu kopyalar ve servisi yeniden baslatir.
#   kullanim: device-files/csi-alpha-webui/deploy.sh admin@<pi-ip>
set -euo pipefail
TARGET="${1:?kullanim: deploy.sh admin@<pi-ip>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROBUST="$HERE/../csi-alpha-robust/home-csi"

scp ${SSH_OPTS:-} "$HERE/webui/csi_web.py" "$HERE/webui/csi_sensing.py" "$HERE/webui/csi_network.py" \
    "$HERE/webui/csi_timeseries.py" "$HERE/webui/csi_nl80211.py" "$HERE/webui/csi_residual.py" "$HERE/webui/app.js" "$HERE/webui/charts.js" \
    "$HERE/webui/index.html" "$HERE/webui/style.css" "$HERE/start_rx_stream.sh" \
    "$ROBUST/timed_reflection_measure.sh" "$ROBUST/net_switch.sh" \
    "$HERE/../csi-alpha-robust/usr-local-sbin/csi-alpha-net-guard.sh" "$TARGET:/tmp/"
ssh ${SSH_OPTS:-} "$TARGET" 'set -e
  sudo cp /tmp/csi_web.py /tmp/csi_sensing.py /tmp/csi_network.py /tmp/csi_timeseries.py /tmp/csi_nl80211.py /tmp/csi_residual.py \
          /tmp/app.js /tmp/charts.js /tmp/index.html /tmp/style.css /home/admin/csi/webui/
  sudo cp /tmp/start_rx_stream.sh /tmp/timed_reflection_measure.sh /tmp/net_switch.sh /home/admin/csi/
  sudo chmod +x /home/admin/csi/start_rx_stream.sh /home/admin/csi/timed_reflection_measure.sh /home/admin/csi/net_switch.sh
  sudo install -m 755 /tmp/csi-alpha-net-guard.sh /usr/local/sbin/csi-alpha-net-guard.sh
  sudo mkdir -p /home/admin/csi/config
  sudo python3 -m py_compile /home/admin/csi/webui/csi_sensing.py /home/admin/csi/webui/csi_network.py /home/admin/csi/webui/csi_timeseries.py /home/admin/csi/webui/csi_nl80211.py /home/admin/csi/webui/csi_residual.py /home/admin/csi/webui/csi_web.py
  sudo systemctl restart csi-web
  sleep 2
  systemctl --no-pager --lines=5 status csi-web
  echo "--- radyo teshisi"
  /usr/sbin/iw dev mgmt0 link | grep -E "SSID|freq|bitrate" || true'
echo "Panel: http://${TARGET#*@}:8080"
