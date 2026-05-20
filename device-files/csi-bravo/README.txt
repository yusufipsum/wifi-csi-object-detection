CSI transmitter script for csi-bravo.

Bravo Wi-Fi MAC for Alpha filter:
  88:a2:9e:5d:4e:a6

Start UDP transmitter toward the default gateway/AP-side network path:
  nohup /home/admin/csi/start_tx.sh 192.168.1.7 5501 >/home/admin/csi/logs/nohup-start-tx.log 2>&1 &

Tunable environment variables:
  RATE_PPS=100 PAYLOAD_SIZE=1200 /home/admin/csi/start_tx.sh 192.168.1.7 5501
