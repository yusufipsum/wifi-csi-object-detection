CSI receiver scripts for csi-alpha.

Bravo Wi-Fi MAC for Alpha source-MAC filter:
  88:a2:9e:5d:4e:a6

Start receiver for a 2 m run:
  nohup sudo /home/admin/csi/start_rx.sh 48/80 88:a2:9e:5d:4e:a6 2 >/home/admin/csi/logs/nohup-start-rx.log 2>&1 &

Start receiver for a 3 m run:
  nohup sudo /home/admin/csi/start_rx.sh 48/80 88:a2:9e:5d:4e:a6 3 >/home/admin/csi/logs/nohup-start-rx.log 2>&1 &

Start receiver for a 5 m run:
  nohup sudo /home/admin/csi/start_rx.sh 48/80 88:a2:9e:5d:4e:a6 5 >/home/admin/csi/logs/nohup-start-rx.log 2>&1 &

Restore normal Wi-Fi/SSH from local Pi terminal:
  sudo /home/admin/csi/restore_wifi.sh

Captures are written to /home/admin/csi/captures/*.pcap with matching *.json metadata.
