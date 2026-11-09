"""Bagli (managed) AX210 arayuzunden nl80211 CMD_FRAME ile Public Action cercevesi gonderir.

Yonetim cerceveleri en dusuk temel hizda (5 GHz: 6 Mbps OFDM legacy, 20 MHz) ve hiz uyarlamasi
olmadan gider; Broadcom/Nexmon okuyabilir. Kanal degismez, baglanti (SSH) etkilenmez.

2026-11-09 olcumu (iwlwifi, kernel 6.12): yayin (DA=ff:..) cerceveler hatasiz kabul edilir ama
havaya cikmaz (Nexmon 0 kare). AP'ye adresli (DA=BSSID) cerceveler gider ve AP ACK'ler
(119/119 ACK, 119/119 Nexmon'da 0xd0). Monitor vif enjeksiyonu da STA bagliyken cikmaz.
"""
import os, socket, struct, time

NETLINK_GENERIC = 16
GENL_ID_CTRL = 0x10
CTRL_CMD_GETFAMILY = 3
CTRL_ATTR_FAMILY_ID = 1
CTRL_ATTR_FAMILY_NAME = 2
NLM_F_REQUEST = 1
NLM_F_ACK = 4
NLMSG_ERROR = 2
NL80211_CMD_FRAME = 59
NL80211_ATTR_IFINDEX = 3
NL80211_ATTR_FRAME = 51
NL80211_ATTR_TX_NO_CCK_RATE = 135
NL80211_ATTR_DONT_WAIT_FOR_ACK = 142


def _attr(kind, data=b""):
    length = 4 + len(data)
    return struct.pack("<HH", length, kind) + data + b"\x00" * ((4 - length % 4) % 4)


def _parse_attrs(buf):
    out = {}
    i = 0
    while i + 4 <= len(buf):
        length, kind = struct.unpack_from("<HH", buf, i)
        if length < 4:
            break
        out[kind & 0x3FFF] = buf[i + 4:i + length]
        i += (length + 3) & ~3
    return out


class Nl80211FrameTx:
    def __init__(self, iface):
        self.iface = iface
        self.ifindex = socket.if_nametoindex(iface)
        self.sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_GENERIC)
        self.sock.bind((0, 0))
        self.sock.settimeout(1.0)
        self.seq = int(time.time()) & 0xFFFF
        self.family = self._resolve("nl80211")

    def close(self):
        self.sock.close()

    def _request(self, msg_type, cmd, payload, ack=True):
        self.seq += 1
        flags = NLM_F_REQUEST | (NLM_F_ACK if ack else 0)
        body = struct.pack("<BBH", cmd, 1, 0) + payload
        self.sock.send(struct.pack("<IHHII", 16 + len(body), msg_type, flags, self.seq, 0) + body)
        return self.seq

    def _recv_until(self, seq):
        """seq'e ait NLMSG_ERROR (ack) gelene kadar oku. Donus: (hata_kodu, son veri mesaji)."""
        data_msg = None
        while True:
            buf = self.sock.recv(65536)
            off = 0
            while off + 16 <= len(buf):
                length, mtype, _flags, mseq, _pid = struct.unpack_from("<IHHII", buf, off)
                if length < 16:
                    return -1, data_msg
                if mseq == seq:
                    if mtype == NLMSG_ERROR:
                        return -struct.unpack_from("<i", buf, off + 16)[0], data_msg
                    data_msg = buf[off + 20:off + length]
                off += (length + 3) & ~3

    def _resolve(self, name):
        seq = self._request(GENL_ID_CTRL, CTRL_CMD_GETFAMILY, _attr(CTRL_ATTR_FAMILY_NAME, name.encode() + b"\x00"))
        err, msg = self._recv_until(seq)
        if err or msg is None:
            raise OSError(err or 1, f"genl family {name} bulunamadi")
        return struct.unpack("<H", _parse_attrs(msg)[CTRL_ATTR_FAMILY_ID][:2])[0]

    def send(self, frame, wait_ack=True):
        payload = (_attr(NL80211_ATTR_IFINDEX, struct.pack("<I", self.ifindex))
                   + _attr(NL80211_ATTR_FRAME, frame)
                   + _attr(NL80211_ATTR_TX_NO_CCK_RATE)
                   + _attr(NL80211_ATTR_DONT_WAIT_FOR_ACK))
        seq = self._request(self.family, NL80211_CMD_FRAME, payload, ack=True)
        if not wait_ack:
            return 0
        err, _ = self._recv_until(seq)
        return err


def _mac(text):
    return bytes(int(x, 16) for x in text.split(":"))


def public_action_frame(src_mac, counter, dst_mac="ff:ff:ff:ff:ff:ff", bssid=None, payload_len=96,
                        oui=b"\x02\xc5\x1a"):
    """Public Action / Vendor Specific (kategori 4, eylem 9). Yerel deneysel OUI; AP yok sayar."""
    bssid = bssid or dst_mac
    header = b"\xd0\x00" + b"\x00\x00" + _mac(dst_mac) + _mac(src_mac) + _mac(bssid) + b"\x00\x00"
    body = bytes([4, 9]) + oui + struct.pack(">Q", counter) + b"\x00" * max(0, payload_len - 8)
    return header + body
