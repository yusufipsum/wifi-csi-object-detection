CSI transmitter script for csi-bravo.

Bravo Wi-Fi MAC for Alpha filter:
  88:a2:9e:5d:4e:a6

Start UDP transmitter toward the default gateway/AP-side network path:
  nohup /home/admin/csi/start_tx.sh 192.168.1.7 5501 >/home/admin/csi/logs/nohup-start-tx.log 2>&1 &

Tunable environment variables:
  TX_PROFILE=multiscale_physical_v1 RATE_PPS=15 PAYLOAD_SIZE=1200 /home/admin/csi/start_tx.sh 192.168.1.7 5501
  CSI_TX_TEE=1 /home/admin/csi/start_tx.sh 192.168.1.7 5501

Multiscale physical defaults:
  TX UDP rate: 15 pps
  Target CSI packetRate in Alpha WebUI: 15-20 pkt/s

The UDP transmit rate is intentionally conservative. In local validation,
RATE_PPS=15 produced Alpha WebUI packetRate close to the 15-20 pkt/s target. If
Alpha WebUI consistently shows below 15 pkt/s, raise RATE_PPS in small steps,
for example 20 or 24. If SSH/WebUI becomes unstable, lower it to 12.

The receiver writes every 6th CSI frame to the ML dataset. The intended model
uses aligned short/long windows, for example 16 samples for hand-scale changes
and 48 samples for wider passage/context changes.

By default the script logs only to /home/admin/csi/logs so it can run cleanly
under nohup/setsid. Use CSI_TX_TEE=1 only for interactive debugging.
