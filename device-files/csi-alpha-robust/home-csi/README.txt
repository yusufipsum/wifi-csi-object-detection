CSI alpha role layout:

  mgmt0 = Intel AX210, managed by NetworkManager, SSH/control path
  csi0  = Broadcom internal Wi-Fi, unmanaged, Nexmon CSI capture path

Bravo MAC filter:
  88:a2:9e:5d:4e:a6

Start a 2 m CSI capture:
  nohup sudo /home/admin/csi/start_rx.sh 48/80 88:a2:9e:5d:4e:a6 2 >/home/admin/csi/logs/nohup-start-rx.log 2>&1 &

Stop CSI capture without touching mgmt0:
  sudo /home/admin/csi/stop_rx.sh

Emergency fallback: use Broadcom as normal Wi-Fi again:
  sudo /home/admin/csi/restore_wifi.sh

Captures:
  /home/admin/csi/captures/*.pcap
  /home/admin/csi/captures/*.json
