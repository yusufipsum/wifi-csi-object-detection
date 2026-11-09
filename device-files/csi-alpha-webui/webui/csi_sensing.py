#!/usr/bin/env python3
"""Tek kutu (quasi-monostatik) Wi-Fi CSI algilama motoru.

Ayni cihazda (csi-alpha) bir radyo (AX210) paket gonderir, digeri (Broadcom +
Nexmon) bu paketlerin CSI'ini toplar. Bu modul, toplanan CSI karelerinden
gercek zamanli olarak su kararlari uretir:

  motion        : ortamda hareketli nesne/insan var mi?
  static        : ortamin statik RF imzasi kalibrasyona gore kalici degisti mi?
                  (nesne birakildi/alindi, kapi/branda acik kaldi ...)
  state         : etiketli durum profillerinden hangisine en yakin? (acik/kapali,
                  dolu/bos ...) - acik kume (open-set) reddi ile
  micro         : (deneysel) hareketsiz insan, solunum bandinda periyodik enerji
  signal/rssi   : sinyal kaybi, anten kapatma/sabotaj

Tasarim kararlari gercek Nexmon (bcm43455c0) verisi uzerinde olculmustur, bkz.
docs/tek-kutu-algilama-tr.md:
  * Ham CSI FFT sirasindadir; DC/guard tonlari cop deger icerir -> ayiklanir.
  * Karelerin ~%20-30'u farkli PHY modunda gelir (BlockAck, Action, farkli
    hiz/akim) -> frame-control filtresi + "mod bankasi" ile ayrilir.
  * Hareket metrigi her PHY modunun KENDI icinde hesaplanir.
  * Statik referans termal/AGC drift'i nedeniyle yavasca uyarlanir.

Bu modul yalnizca standart kutuphane kullanir (Pi uzerinde numpy sart degil).
"""

import cmath
import collections
import json
import math
import struct
import time

try:
    import csi_residual
except ImportError:  # modul yoksa sizinti iptalli kanal devre disi
    csi_residual = None

ENGINE_VERSION = "single_box_v1"

# ---------------------------------------------------------------------------
# Konfigurasyon semasi, varsayilanlar ve senaryo on-ayarlari
# ---------------------------------------------------------------------------

SCENARIOS = {
    "room": {
        "label": "Oda / iç mekân (insan hareketi)",
        "description": "Kapalı alanda insan/nesne hareketi ve kalıcı ortam değişimi. "
                       "Mobilya değişirse referans 15 dk sonra kendini yeniler.",
        "overrides": {
            "staticAutoRelearnS": 900,
            "stateAlarmLabels": "",
            "microEnabled": False,
        },
    },
    "vehicle_cargo": {
        "label": "Kamyonet / araç kasası güvenliği",
        "description": "Park hâlindeki kasada izinsiz açılma, içeride hareket, saklanan kişi "
                       "(mikro-hareket) ve cihaz sabotajı. Statik değişim kendiliğinden silinmez.",
        "overrides": {
            "staticAutoRelearnS": 0,
            "staticAdaptTauS": 600,
            "stateAlarmLabels": "open,acik,branda_acik,kesik",
            "microEnabled": True,
            "rssiDropEnabled": True,
            "signalLossS": 3.0,
            "alarmCooldownS": 30.0,
        },
    },
    "door": {
        "label": "Kapı / geçiş noktası",
        "description": "Kapı açık/kapalı durumu ve geçiş hareketi. Kısa pencere, hızlı tepki.",
        "overrides": {
            "motionWindowS": 1.5,
            "staticWindowS": 3.0,
            "staticQuietS": 1.0,
            "staticAutoRelearnS": 0,
            "stateAlarmLabels": "open,acik",
        },
    },
    "warehouse": {
        "label": "Depo / raf (statik nesne varlığı)",
        "description": "Nesne bırakılması/alınması gibi kalıcı değişimlere odaklı; "
                       "hareket daha az hassas, statik pencere uzun.",
        "overrides": {
            "motionScale": 1.5,
            "staticScale": 0.8,
            "staticWindowS": 8.0,
            "staticMinDist": 2.5,
            "staticAdaptTauS": 1800,
            "staticAutoRelearnS": 0,
            "stateAlarmLabels": "",
        },
    },
    "custom": {
        "label": "Özel",
        "description": "Hiçbir ön-ayar uygulanmaz; tüm parametreler elle.",
        "overrides": {},
    },
}

# key, type, default, min, max, group, label, help, live (calisirken degistirilebilir mi)
_S = []


def _p(key, typ, default, lo=None, hi=None, group="Genel", label="", help_text="", live=True, options=None):
    _S.append({
        "key": key,
        "type": typ,
        "default": default,
        "min": lo,
        "max": hi,
        "group": group,
        "label": label or key,
        "help": help_text,
        "live": live,
        "options": options,
    })


_p("scenario", "enum", "room", group="Genel", label="Senaryo",
   help_text="Ön-ayar seti. Değiştirilince ilgili varsayılanlar uygulanır.",
   options=list(SCENARIOS.keys()))
_p("armed", "bool", True, group="Genel", label="Alarm kurulu",
   help_text="Kapalıyken kararlar hesaplanır ama alarm kaydı üretilmez.")

_p("txBackend", "enum", "ax210_action", group="Radyo", label="TX yöntemi",
   help_text="ax210_action (önerilen, gerçek tek kutu): AX210 bağlıyken erişim noktasına adresli küçük Public Action "
             "yönetim çerçevesi yollar; sabit 6 Mbps legacy, hız/güç uyarlaması yok, modemin cevabına bağlı değil. "
             "ax210_ba: Pi ağ geçidine hızlı ping atar, AX210 her cevaba BlockAck yayar; ölçüm kısmen AX210–modem "
             "bağlantısını yansıtır (2026-11-09 bulgusu). "
             "ap_udp: AX210'un UDP veri çerçeveleri (yalnız 802.11ac/80 MHz bağlantıda okunabilir). "
             "inject: AX210 monitor modda enjeksiyon (AX210 bağlıyken iwlwifi çerçeveyi göndermiyor; yalnız bağlantısızken).",
   live=False, options=["ax210_ba", "ax210_action", "ap_udp", "inject"])
_p("channel", "str", "auto", group="Radyo", label="Kanal (chanspec)",
   help_text="'auto' = AX210'un bağlı olduğu ana kanal okunur: ax210_ba/ax210_action/inject için <kanal>/20, ap_udp için <kanal>/80.",
   live=False)
_p("txRatePps", "float", 50.0, 5.0, 200.0, "Radyo", "TX / sondalama hızı (pps)",
   "Saniyedeki sondalama sayısı = CSI kare hızı. Ölçüm: 50 pps → 47 kare/sn. 30-80 önerilir.", live=False)
_p("probeTarget", "str", "auto", group="Radyo", label="Sondalama hedefi",
   help_text="ax210_ba için ping hedefi. 'auto' = varsayılan ağ geçidi (modem).", live=False)
_p("txPayloadBytes", "int", 600, 64, 1400, "Radyo", "TX yük boyu (bayt)", live=False)
_p("injectChannel", "int", 36, 1, 165, "Radyo", "Enjeksiyon kanalı",
   "Yalnız inject modunda kullanılır.", live=False)
_p("injectRateMbps", "float", 6.0, 6.0, 54.0, "Radyo", "Enjeksiyon hızı (Mbps)",
   "Legacy OFDM hızı; 6 Mbps en sağlam.", live=False)
_p("fcFilter", "str", "auto", group="Radyo", label="Frame-control filtresi",
   help_text="Kabul edilen 802.11 frame-control baytları (virgüllü). 'auto' = TX yöntemine göre "
             "(ax210_action → 0xd0 Action; ax210_ba → 0x94 BlockAck; ap_udp → 0x88,0x08; inject → 0x08). Boş = hepsi.")

_p("toneGroups", "int", 56, 16, 128, "Ön işleme", "Ton grubu sayısı",
   "Geçerli alt taşıyıcılar bu kadar gruba ortalanır (gürültü ↓). Değişirse yeniden kalibrasyon gerekir.")
_p("modeMatch", "float", 0.90, 0.5, 0.99, "Ön işleme", "PHY modu eşleşme korelasyonu",
   "Bir karenin mevcut moda ait sayılması için gereken şekil korelasyonu.")
_p("maxModes", "int", 6, 1, 12, "Ön işleme", "En fazla mod sayısı")
_p("phaseWeight", "float", 0.5, 0.0, 2.0, "Ön işleme", "Faz ağırlığı (statik)",
   "Statik/durum mesafesinde temizlenmiş faz şeklinin ağırlığı. 0 = sadece genlik.")
_p("hopS", "float", 0.5, 0.2, 2.0, "Ön işleme", "Karar aralığı (sn)")

_p("calibrationS", "int", 180, 10, 1800, "Kalibrasyon", "Kalibrasyon süresi (sn)",
   "Ortam 'normal' durumdayken (boş/kapalı) toplanır. 180-300 sn önerilir: T² eşiği boş-oda dağılımının "
   "kuyruğundan çıkar; 60 sn kuyruğu hafife aldı (2026-11-09: kalibrasyon p99 1.25, boş oda p99 2.0).")
_p("calMadK", "float", 8.0, 2.0, 30.0, "Kalibrasyon", "Robust eşik katsayısı (k·MAD)")
_p("calPercentile", "float", 99.5, 90.0, 100.0, "Kalibrasyon", "Eşik yüzdeliği")
_p("calMargin", "float", 1.2, 1.0, 3.0, "Kalibrasyon", "Yüzdelik payı")

_p("motionEnabled", "bool", True, group="Hareket", label="Hareket algılama")
_p("motionWindowS", "float", 2.0, 0.5, 10.0, "Hareket", "Hareket penceresi (sn)")
_p("motionScale", "float", 1.0, 0.2, 5.0, "Hareket", "Eşik çarpanı",
   "<1 daha hassas, >1 daha az hassas.")
_p("motionMinThr", "float", 0.002, 0.0, 0.1, "Hareket", "Mutlak alt eşik (log10)")
_p("noveltyEnabled", "bool", True, group="Hareket", label="Yenilik (mod dışı kare) skoru",
   help_text="Yakın hareket CSI şeklini bilinen modların dışına iter; bu oran da hareket kanıtıdır.")
_p("motionPowerEnabled", "bool", False, group="Hareket", label="Ortak-mod güç kanalı",
   help_text="Kare gücünün ardışık farkı da hareket kanıtı sayılır. 20 MHz/2.4 GHz kurulumlarda faydalı olabilir; "
             "80 MHz gerçek veride katkısı ihmal edilebilir bulundu (AGC gürültüsüne duyarlı).")
_p("gainStateEnabled", "bool", True, group="Hareket", label="Kazanç/verici durumu geçişi",
   help_text="Yalnız ax210_ba'da etkin. Pencerede baskın RSSI durumunun dışına düşen karelerin payı. AX210 "
             "BlockAck'leri, modemle bağlantısı bozulunca ayrık güç durumları arasında geçer (ölçülen: −24↔−20 dBm). "
             "2026-11-09'da kapı+insan olaylarının 17/17'sini yakaladı; ama bu, yansımayı değil modem bağlantısını "
             "ölçüyor. ax210_action'da olaylara tepki vermediği için orada kapalı.")
_p("gainStateTolDb", "float", 2.0, 0.5, 10.0, "Hareket", "Durum toleransı (dB)",
   "Baskın RSSI değerine bu kadar yakın kareler aynı durum sayılır.")
_p("gainStateMaxDb", "float", 12.0, 3.0, 40.0, "Hareket", "Durum üst sınırı (dB)",
   "Bundan uzak kareler (başka verici, bozuk kare) hesaba katılmaz.")
_p("gainMinShare", "float", 0.03, 0.0, 0.5, "Hareket", "Durum payı alt eşiği",
   "Kalibrasyon eşiği bundan küçük çıkarsa bu kullanılır (50 kare/sn·2 sn'de ≈3 kare).")
_p("residualEnabled", "bool", True, group="Hareket", label="Sızıntı iptalli değişim (T²)",
   help_text="Doğrudan AX210→Broadcom sızıntısı her karede en küçük kareler izdüşümüyle sıfırlanır; artık, boş-oda "
             "kalibrasyonunun kovaryansıyla beyazlatılır ve son W sn'nin ortalaması önceki B sn'ye göre test edilir "
             "(Hotelling türü, boş odada ≈1). 2026-11-09 ax210_action kaydı: yakın 8/8, uzak 10/12, 15 dk boş odada 1 yanlış küme.")
_p("residualWindowS", "float", 3.0, 0.5, 10.0, "Hareket", "T² pencere W (sn)")
_p("residualBaseS", "float", 20.0, 5.0, 120.0, "Hareket", "T² taban B (sn)",
   "Karşılaştırma tabanı; durum değişiminden sonra bu kadar süre yerleşme beklenir.")
_p("residualTauS", "float", 30.0, 0.0, 600.0, "Hareket", "Sızıntı referansı izleme τ (sn)",
   "Her verici durumunun sızıntı vektörü bu zaman sabitiyle güncellenir (0 = sabit).")
_p("residualMinThr", "float", 2.0, 1.0, 50.0, "Hareket", "T² alt eşik",
   "Kalibrasyon eşiği bundan küçükse bu kullanılır (boş odada beklenen değer ≈1).")
_p("motionOnK", "int", 3, 1, 20, "Hareket", "Tetik: son N kararın K'sı")
_p("motionOnN", "int", 4, 1, 30, "Hareket", "Tetik penceresi N")
_p("motionOffCount", "int", 4, 1, 60, "Hareket", "Bitiş için ardışık sakin karar")

_p("staticEnabled", "bool", True, group="Statik değişim", label="Statik değişim algılama")
_p("staticWindowS", "float", 4.0, 1.0, 30.0, "Statik değişim", "Statik pencere (sn)")
_p("staticScale", "float", 1.0, 0.2, 5.0, "Statik değişim", "Eşik çarpanı")
_p("staticMinDist", "float", 3.0, 0.5, 20.0, "Statik değişim", "Asgari z-mesafe eşiği",
   "Gerçek veride 15 dk boş ortamda uyarlamalı referansla mesafe ≤2.2 kaldı.")
_p("staticQuietS", "float", 2.0, 0.0, 30.0, "Statik değişim", "Gereken sessizlik (sn)",
   "Statik karar yalnızca bu kadar süre hareket yokken güncellenir (hareket statik sanılmasın).")
_p("staticAdaptTauS", "float", 180.0, 0.0, 7200.0, "Statik değişim", "Referans uyarlama τ (sn)",
   "Sessiz ve normalken referans bu zaman sabitiyle drift'i izler. 0 = kapalı.")
_p("rebaselineOnStart", "bool", True, group="Statik değişim", label="Kurulumda referansı tazele",
   help_text="Başlatınca/kurunca ilk sessiz pencere statik referans olur (hareket eşikleri kalibrasyondan kalır). "
             "Saatler önceki kalibrasyonun termal drift'i sahte statik alarm üretmesin diye. Ortam o an 'normal' olmalı.")
_p("staticAutoRelearnS", "float", 0.0, 0.0, 86400.0, "Statik değişim", "Otomatik yeni referans (sn)",
   "Statik alarm hareketsiz bu kadar sürerse yeni durum normal kabul edilir. 0 = asla.")
_p("staticOnK", "int", 4, 1, 30, "Statik değişim", "Tetik K")
_p("staticOnN", "int", 6, 1, 40, "Statik değişim", "Tetik N")
_p("staticOffCount", "int", 6, 1, 120, "Statik değişim", "Bitiş sayısı")

_p("stateEnabled", "bool", True, group="Durum profilleri", label="Durum sınıflandırma")
_p("stateAlarmLabels", "str", "open,acik", group="Durum profilleri", label="Alarm etiketleri",
   help_text="Bu etiketlerden birine geçişte alarm üretilir (virgüllü).")
_p("openSetFactor", "float", 2.0, 1.0, 10.0, "Durum profilleri", "Bilinmeyen eşiği (×p99)",
   "En yakın profile mesafe, profilin kendi p99 mesafesinin bu katını aşarsa 'bilinmeyen'.")
_p("stateMargin", "float", 0.10, 0.0, 1.0, "Durum profilleri", "Karar marjı")
_p("stateOnK", "int", 3, 1, 20, "Durum profilleri", "Kararlılık K")
_p("stateOnN", "int", 4, 1, 30, "Durum profilleri", "Kararlılık N")

_p("microEnabled", "bool", False, group="Mikro-hareket", label="Mikro-hareket (deneysel)",
   help_text="Hareketsiz insanın solunumundan kaynaklı periyodik enerji. Gerçek veride henüz doğrulanmadı.")
_p("microWindowS", "float", 20.0, 8.0, 60.0, "Mikro-hareket", "Pencere (sn)")
_p("microBandLowHz", "float", 0.15, 0.05, 1.0, "Mikro-hareket", "Bant alt (Hz)")
_p("microBandHighHz", "float", 0.6, 0.2, 2.0, "Mikro-hareket", "Bant üst (Hz)")
_p("microScale", "float", 1.0, 0.2, 5.0, "Mikro-hareket", "Eşik çarpanı")
_p("microOnK", "int", 3, 1, 20, "Mikro-hareket", "Tetik K")
_p("microOnN", "int", 4, 1, 30, "Mikro-hareket", "Tetik N")

_p("signalLossEnabled", "bool", True, group="Sinyal / sabotaj", label="Sinyal kaybı alarmı")
_p("minPacketRate", "float", 5.0, 0.5, 100.0, "Sinyal / sabotaj", "Asgari geçerli kare hızı (/sn)")
_p("signalLossS", "float", 5.0, 1.0, 120.0, "Sinyal / sabotaj", "Sinyal kaybı süresi (sn)")
_p("rssiDropEnabled", "bool", False, group="Sinyal / sabotaj", label="RSSI düşüşü (anten kapatma)")
_p("rssiDropDb", "float", 10.0, 2.0, 40.0, "Sinyal / sabotaj", "RSSI düşüşü (dB)")
_p("rssiHoldS", "float", 5.0, 1.0, 120.0, "Sinyal / sabotaj", "RSSI düşüş süresi (sn)")

_p("alarmCooldownS", "float", 10.0, 0.0, 3600.0, "Alarm", "Aynı alarm için bekleme (sn)")
_p("releaseRatio", "float", 0.9, 0.1, 1.0, "Alarm", "Bırakma oranı (histerezis)",
   "Aktif alarm, skor eşiğin bu katının altına inince bitiş sayımı başlar. Kalibre eşikte normal skor ~0.6-0.75'tir.")

CONFIG_SCHEMA = _S
_SCHEMA_BY_KEY = {item["key"]: item for item in CONFIG_SCHEMA}

# Kalibrasyon sonuclarini gecersiz kilan ayarlar
CALIBRATION_SENSITIVE_KEYS = ("toneGroups", "motionWindowS", "staticWindowS", "microWindowS",
                              "microBandLowHz", "microBandHighHz")


def default_config(scenario="room"):
    config = {item["key"]: item["default"] for item in CONFIG_SCHEMA}
    if scenario not in SCENARIOS:
        scenario = "room"
    config.update(SCENARIOS[scenario]["overrides"])
    config["scenario"] = scenario
    return config


def _coerce(item, value):
    typ = item["type"]
    if typ == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on", "evet")
        return bool(value)
    if typ == "int":
        value = int(round(float(value)))
    elif typ == "float":
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("not finite")
    elif typ == "enum":
        value = str(value)
        if value not in (item.get("options") or []):
            raise ValueError(f"must be one of {item.get('options')}")
        return value
    else:
        return str(value).strip()[:200]
    if item.get("min") is not None:
        value = max(item["min"], value)
    if item.get("max") is not None:
        value = min(item["max"], value)
    return value


def normalize_config(raw=None, base=None):
    """Gelen sozlugu semaya gore dogrular. (config, errors) dondurur.

    Bilinmeyen anahtarlar yok sayilir; sinir disi degerler sinira cekilir.
    """
    config = dict(base) if base else default_config()
    for key, item in _SCHEMA_BY_KEY.items():
        config.setdefault(key, item["default"])
    errors = {}
    for key, value in (raw or {}).items():
        item = _SCHEMA_BY_KEY.get(key)
        if item is None:
            continue
        try:
            config[key] = _coerce(item, value)
        except (TypeError, ValueError) as exc:
            errors[key] = str(exc)
    if config["microBandHighHz"] <= config["microBandLowHz"]:
        config["microBandHighHz"] = min(2.0, config["microBandLowHz"] + 0.2)
    if config["motionOnK"] > config["motionOnN"]:
        config["motionOnK"] = config["motionOnN"]
    if config["staticOnK"] > config["staticOnN"]:
        config["staticOnK"] = config["staticOnN"]
    if config["stateOnK"] > config["stateOnN"]:
        config["stateOnK"] = config["stateOnN"]
    if config["microOnK"] > config["microOnN"]:
        config["microOnK"] = config["microOnN"]
    return config, errors


def apply_scenario(config, scenario):
    """Mevcut config'e senaryo on-ayarlarini uygular (diger ayarlar korunur)."""
    if scenario not in SCENARIOS:
        return config
    merged = dict(config)
    merged.update(SCENARIOS[scenario]["overrides"])
    merged["scenario"] = scenario
    return normalize_config(merged, base=default_config(scenario))[0]


def parse_fc_filter(text):
    values = set()
    for part in str(text or "").replace(";", ",").split(","):
        part = part.strip().lower()
        if not part:
            continue
        try:
            values.add(int(part, 16) if part.startswith("0x") else int(part))
        except ValueError:
            continue
    return values


AUTO_FC_FILTER = {"ax210_ba": {0x94}, "ax210_action": {0xD0}, "ap_udp": {0x88, 0x08}, "inject": {0x08}}


def resolve_fc_filter(text, backend):
    if str(text or "").strip().lower() == "auto":
        return set(AUTO_FC_FILTER.get(backend, {0x88, 0x08}))
    return parse_fc_filter(text)


def parse_label_list(text):
    return [part.strip().lower() for part in str(text or "").replace(";", ",").split(",") if part.strip()]


# ---------------------------------------------------------------------------
# Kucuk istatistik yardimcilari
# ---------------------------------------------------------------------------

def _mean(values):
    return sum(values) / len(values) if values else 0.0


def _median(values):
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def _percentile(values, q):
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * max(0.0, min(100.0, q)) / 100.0
    lo = int(math.floor(pos))
    hi = min(len(ordered) - 1, lo + 1)
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def _mad(values, center=None):
    if not values:
        return 0.0
    if center is None:
        center = _median(values)
    return _median([abs(v - center) for v in values])


def _wrap(angle):
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def robust_threshold(values, k, percentile, margin, floor):
    """thr = max(median + k*1.4826*MAD, P_q * margin, floor)."""
    if not values:
        return floor
    med = _median(values)
    mad = 1.4826 * _mad(values, med)
    return max(med + k * mad, _percentile(values, percentile) * margin, floor)


def _round_list(values, digits=6):
    return [round(float(v), digits) for v in values]


# ---------------------------------------------------------------------------
# Ton duzeni ve kare on-isleme
# ---------------------------------------------------------------------------

# Gecerli veri tonlari (|k| araligi) ve pilot tonlari. 20 MHz icin legacy (±26)
# alt kumesi kullanilir; boylece hem legacy hem HT/VHT 20 MHz cercevelerde gecerli.
_TONE_RANGE = {64: (1, 26), 128: (2, 58), 256: (2, 122)}
_PILOTS = {64: (7, 21), 128: (11, 25, 53), 256: (11, 39, 75, 103)}


class ToneLayout:
    """Nexmon ham CSI (FFT sirasi) -> gecerli tonlar (artan k) -> gruplar."""

    def __init__(self, nfft, groups):
        lo, hi = _TONE_RANGE[nfft]
        pilots = set(_PILOTS[nfft])
        ks = [k for k in range(-hi, hi + 1) if lo <= abs(k) <= hi and abs(k) not in pilots]
        self.nfft = nfft
        self.ks = ks
        self.fft_index = [k % nfft for k in ks]
        self.adjacent = [i for i in range(len(ks) - 1) if ks[i + 1] - ks[i] == 1]
        count = max(1, min(int(groups), len(ks)))
        n = len(ks)
        self.groups = [list(range(int(g * n / count), int((g + 1) * n / count))) for g in range(count)]
        self.group_count = count

    @property
    def valid_tones(self):
        return len(self.ks)


def nfft_from_payload_len(csi_len):
    words = csi_len // 4
    for nfft in (256, 128, 64):
        if words >= nfft:
            return nfft
    return 0


class Frame:
    __slots__ = ("t", "mode", "novel", "amp", "unit", "phase", "rssi", "power", "level", "cvec")

    def __init__(self, t, amp, unit, phase, rssi, power, level=0.0, cvec=None):
        self.t = t
        self.mode = -1
        self.novel = False
        self.amp = amp
        self.unit = unit
        self.phase = phase
        self.rssi = rssi
        self.power = power
        self.level = level
        self.cvec = cvec


def extract_frame(iq, nfft, layout, t=0.0, rssi=0.0):
    """Ham I/Q baytlarindan gruplanmis ozellik cikarir.

    Donus: (Frame, None) veya (None, red_nedeni)
      amp   : G grup log10 genlik, kare ortalamasi cikarilmis (AGC/guc bagimsiz sekil)
      unit  : amp / ||amp|| (mod eslestirme icin birim vektor)
      phase : G grup temizlenmis faz (STO egimi + ortak faz cikarilmis)
    """
    needed = 4 * nfft
    if not iq or len(iq) < needed:
        return None, "short"
    vals = struct.unpack_from("<%dh" % (2 * nfft), iq, 0)
    tones = [complex(vals[2 * i], vals[2 * i + 1]) for i in layout.fft_index]
    zeros = 0
    amps = []
    for h in tones:
        a = abs(h)
        if a == 0.0:
            zeros += 1
        amps.append(a)
    if zeros > 0.1 * len(tones):
        return None, "zeros"
    power = sum(amps) / len(amps)
    if power < 2.0:
        return None, "weak"
    logs = [math.log10(a) if a > 1.0 else 0.0 for a in amps]
    g_amp = []
    for grp in layout.groups:
        g_amp.append(sum(logs[i] for i in grp) / len(grp))
    center = sum(g_amp) / len(g_amp)
    g_amp = [v - center for v in g_amp]
    norm = math.sqrt(sum(v * v for v in g_amp))
    if norm < 1e-9:
        return None, "flat"
    unit = [v / norm for v in g_amp]

    # Faz temizleme: komsu tonlarin H_{k+1} H_k^* toplaminin acisi = STO egimi
    # (unwrap gerektirmez, gurultuye dayanikli). Sonra ortak faz (CPE) cikarilir.
    acc = 0j
    for i in layout.adjacent:
        acc += tones[i + 1] * tones[i].conjugate()
    slope = cmath.phase(acc) if acc != 0 else 0.0
    rotated = [h * cmath.exp(-1j * slope * k) for h, k in zip(tones, layout.ks)]
    total = sum(rotated)
    cpe = cmath.exp(-1j * cmath.phase(total)) if total != 0 else 1.0
    g_phase = []
    for grp in layout.groups:
        s = 0j
        for i in grp:
            s += rotated[i]
        g_phase.append(cmath.phase(s * cpe) if s != 0 else 0.0)
    return Frame(t, g_amp, unit, g_phase, float(rssi), power, center, rotated), None


# ---------------------------------------------------------------------------
# PHY mod bankasi
# ---------------------------------------------------------------------------

class ModeBank:
    """Cevrimici sekil kumeleme. Ayni vericiden gelen cerceveler farkli PHY
    modlarinda (hiz, akim, duplicate) farkli CSI sekli tasir; hareket ve statik
    karsilastirma her modun kendi icinde yapilir."""

    NOVEL_COUNT = 20

    def __init__(self, match=0.9, max_modes=6, lr=0.01):
        self.match = float(match)
        self.max_modes = int(max_modes)
        self.lr = float(lr)
        self.modes = []
        self.next_id = 0
        self.tick = 0

    def assign(self, unit):
        self.tick += 1
        best = None
        best_corr = -2.0
        for mode in self.modes:
            corr = sum(a * b for a, b in zip(unit, mode["c"]))
            if corr > best_corr:
                best, best_corr = mode, corr
        if best is not None and best_corr >= self.match:
            lr = max(self.lr, 1.0 / (best["count"] + 1.0))
            c = [(1.0 - lr) * a + lr * b for a, b in zip(best["c"], unit)]
            norm = math.sqrt(sum(v * v for v in c)) or 1.0
            best["c"] = [v / norm for v in c]
            best["count"] += 1
            best["last"] = self.tick
            return best["id"], best_corr, best["count"] < self.NOVEL_COUNT
        if len(self.modes) >= self.max_modes:
            victim = min(self.modes, key=lambda m: m["last"])
            self.modes.remove(victim)
        mode = {"id": self.next_id, "c": list(unit), "count": 1, "last": self.tick}
        self.next_id += 1
        self.modes.append(mode)
        return mode["id"], best_corr, True

    def centroid(self, mode_id):
        for mode in self.modes:
            if mode["id"] == mode_id:
                return mode["c"]
        return None

    def summary(self):
        return [{"id": m["id"], "count": m["count"]} for m in sorted(self.modes, key=lambda m: -m["count"])]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


# ---------------------------------------------------------------------------
# Pencere istatistikleri
# ---------------------------------------------------------------------------

def within_mode_motion(frames, min_frames=5):
    """Her mod icin grup-basina zamansal std'nin medyani; kare sayisi agirlikli ortalama."""
    by_mode = collections.defaultdict(list)
    for f in frames:
        if f.mode >= 0:
            by_mode[f.mode].append(f)
    num = 0.0
    den = 0
    for items in by_mode.values():
        n = len(items)
        if n < min_frames:
            continue
        G = len(items[0].amp)
        stds = []
        for g in range(G):
            s = 0.0
            s2 = 0.0
            for f in items:
                v = f.amp[g]
                s += v
                s2 += v * v
            m = s / n
            stds.append(math.sqrt(max(0.0, s2 / n - m * m)))
        num += _median(stds) * n
        den += n
    if den == 0:
        return None
    return num / den


def within_mode_power(frames, min_frames=5):
    """Mod icinde kare seviyesinin (ortak-mod log guc) ardisik farklarinin medyani."""
    by_mode = collections.defaultdict(list)
    for f in frames:
        if f.mode >= 0:
            by_mode[f.mode].append(f.level)
    num = 0.0
    den = 0
    for levels in by_mode.values():
        n = len(levels)
        if n < min_frames:
            continue
        num += _median([abs(b - a) for a, b in zip(levels, levels[1:])]) * n
        den += n
    return (num / den) if den else None


def gain_state_share(frames, tol_db=2.0, max_db=12.0, min_frames=10):
    """Penceredeki karelerin baskin RSSI durumunun disinda kalan payi (0..0.5).

    Baskin durum = en sik RSSI degeri +-tol_db. Ondan tol_db..max_db uzaktaki kareler
    "azinlik durum"dur. Ayni vericinin ayrik guc/AGC durumlari arasinda gecisi olcer:
    tek kutuda AX210 BlockAck'leri ortam degisince -24/-20 dBm durumlari arasinda gecer,
    sekli neredeyse ayni kalir (r=0.997) - bu yuzden sekil metrikleri bunu goremez.
    RSSI yoksa (hepsi 0) None doner."""
    vals = [int(round(f.rssi)) for f in frames if f.rssi]
    if len(vals) < min_frames:
        return None
    dom = collections.Counter(vals).most_common(1)[0][0]
    inside = 0
    minority = 0
    for v in vals:
        d = abs(v - dom)
        if d <= tol_db:
            inside += 1
        elif d <= max_db:
            minority += 1
    total = inside + minority
    return (minority / total) if total >= min_frames else None


def significant_modes(current_modes, min_frames=8, min_share=0.10):
    """Statik karara katilacak modlar. Sinirdaki gecici modlar (az kare) secilim nedeniyle
    yanli ortalama tasir; mesafeyi sisirmesinler diye disarida birakilir."""
    total = sum(cur["n"] for cur in current_modes.values())
    return {m: cur for m, cur in current_modes.items()
            if cur["n"] >= min_frames and cur["n"] >= min_share * total}


def mode_means(frames, min_frames=5):
    """Mod basina ortalama genlik sekli ve dairesel ortalama faz."""
    by_mode = collections.defaultdict(list)
    for f in frames:
        if f.mode >= 0 and not f.novel:
            by_mode[f.mode].append(f)
    result = {}
    for mode, items in by_mode.items():
        n = len(items)
        if n < min_frames:
            continue
        G = len(items[0].amp)
        amp = [sum(f.amp[g] for f in items) / n for g in range(G)]
        phase = []
        for g in range(G):
            s = 0j
            for f in items:
                s += cmath.exp(1j * f.phase[g])
            phase.append(cmath.phase(s) if s != 0 else 0.0)
        unit_norm = math.sqrt(sum(v * v for v in amp)) or 1.0
        result[mode] = {"amp": amp, "phase": phase, "n": n, "unit": [v / unit_norm for v in amp]}
    return result


def ref_distance(cur, ref, phase_weight):
    """Diagonal z-mesafe (RMS). Genlik ve dairesel faz farki, kalibrasyon pencere sapmasina gore."""
    za = 0.0
    for a, m, s in zip(cur["amp"], ref["amp"], ref["ampSd"]):
        z = (a - m) / s
        za += z * z
    zp = 0.0
    if phase_weight > 0:
        for p, m, s in zip(cur["phase"], ref["phase"], ref["phaseSd"]):
            z = _wrap(p - m) / s
            zp += z * z
    G = len(cur["amp"])
    return math.sqrt((za + phase_weight * zp) / (G * (1.0 + phase_weight)))


def build_mode_references(frames, window_s, phase_weight=0.5, min_share=0.05, min_frames=30,
                          amp_sd_floor=0.0015, phase_sd_floor=0.015):
    """Kalibrasyon/profil kareleri -> mod basina referans (ortalama, pencere-seviyesi sapma)."""
    by_mode = collections.defaultdict(list)
    for f in frames:
        if f.mode >= 0 and not f.novel:
            by_mode[f.mode].append(f)
    total = sum(len(v) for v in by_mode.values())
    refs = []
    for mode, items in sorted(by_mode.items(), key=lambda kv: -len(kv[1])):
        if len(items) < min_frames or len(items) < min_share * max(1, total):
            continue
        G = len(items[0].amp)
        # pencere-seviyesi parcalar (canli karar da pencere ortalamasiyla verilir)
        chunks = []
        start = items[0].t
        bucket = []
        for f in items:
            if f.t - start >= window_s and bucket:
                chunks.append(bucket)
                bucket = []
                start = f.t
            bucket.append(f)
        if bucket:
            chunks.append(bucket)
        chunk_stats = [mode_means(c, min_frames=3) for c in chunks]
        chunk_stats = [cs[mode] for cs in chunk_stats if mode in cs]
        all_stats = mode_means(items, min_frames=1)[mode]
        amp = all_stats["amp"]
        phase = all_stats["phase"]
        if len(chunk_stats) >= 3:
            amp_sd = []
            ph_sd = []
            for g in range(G):
                da = [c["amp"][g] - amp[g] for c in chunk_stats]
                dp = [_wrap(c["phase"][g] - phase[g]) for c in chunk_stats]
                amp_sd.append(math.sqrt(sum(v * v for v in da) / len(da)))
                ph_sd.append(math.sqrt(sum(v * v for v in dp) / len(dp)))
        else:
            # az parca: kare-seviyesi sapma / sqrt(pencere basina kare)
            per_window = max(1.0, len(items) / max(1.0, (items[-1].t - items[0].t) / window_s))
            amp_sd = []
            ph_sd = []
            for g in range(G):
                da = [f.amp[g] - amp[g] for f in items]
                dp = [_wrap(f.phase[g] - phase[g]) for f in items]
                amp_sd.append(math.sqrt(sum(v * v for v in da) / len(da) / per_window))
                ph_sd.append(math.sqrt(sum(v * v for v in dp) / len(dp) / per_window))
        amp_sd = [max(amp_sd_floor, v) for v in amp_sd]
        ph_sd = [max(phase_sd_floor, v) for v in ph_sd]
        norm = math.sqrt(sum(v * v for v in amp)) or 1.0
        ref = {
            "unit": [v / norm for v in amp],
            "amp": amp,
            "ampSd": amp_sd,
            "phase": phase,
            "phaseSd": ph_sd,
            "frames": len(items),
            "share": len(items) / max(1, total),
        }
        ref["chunkDistances"] = [ref_distance(c, ref, phase_weight) for c in chunk_stats]
        refs.append(ref)
    return refs


def match_reference(unit, refs, min_corr):
    best = None
    best_corr = -2.0
    for ref in refs:
        corr = _dot(unit, ref["unit"])
        if corr > best_corr:
            best, best_corr = ref, corr
    if best is None or best_corr < min_corr:
        return None, best_corr
    return best, best_corr


def weighted_distance(current_modes, refs, phase_weight, min_corr):
    """Penceredeki tum modlar icin referansa mesafe; kare sayisi agirlikli. Eslesmeyen mod payi da dondurulur."""
    num = 0.0
    den = 0
    unmatched = 0
    matches = []
    for mode, cur in current_modes.items():
        ref, corr = match_reference(cur["unit"], refs, min_corr)
        if ref is None:
            unmatched += cur["n"]
            continue
        d = ref_distance(cur, ref, phase_weight)
        num += d * cur["n"]
        den += cur["n"]
        matches.append((ref, cur, d))
    total = den + unmatched
    if den == 0:
        return None, (unmatched / total if total else 0.0), matches
    return num / den, (unmatched / total if total else 0.0), matches


# ---------------------------------------------------------------------------
# Mikro-hareket (solunum bandi) - deneysel
# ---------------------------------------------------------------------------

def micro_motion_metric(frames, window_s, band_lo, band_hi, fs=4.0, top_groups=8):
    """Birincil moddaki en degisken gruplarin, duzgun orneklenmis zaman serisinde
    bant-ici tepe gucu / tum bantlarin medyan gucu (peak-to-median). Olcek bagimsiz."""
    by_mode = collections.Counter(f.mode for f in frames if f.mode >= 0 and not f.novel)
    if not by_mode:
        return None
    mode, count = by_mode.most_common(1)[0]
    items = [f for f in frames if f.mode == mode and not f.novel]
    if count < window_s * 2.0 or len(items) < 16:
        return None
    t0 = items[0].t
    span = items[-1].t - t0
    if span < 0.8 * window_s:
        return None
    G = len(items[0].amp)
    variances = []
    for g in range(G):
        vals = [f.amp[g] for f in items]
        m = _mean(vals)
        variances.append((sum((v - m) ** 2 for v in vals) / len(vals), g))
    variances.sort(reverse=True)
    groups = [g for _, g in variances[:top_groups]]
    n = int(span * fs)
    if n < 16:
        return None
    times = [t0 + i / fs for i in range(n)]
    hann = [0.5 - 0.5 * math.cos(2.0 * math.pi * i / (n - 1)) for i in range(n)]
    freqs = []
    j = 1
    while True:
        f = j / (n / fs)
        if f > min(1.9, fs / 2.0 - 0.05):
            break
        if f >= 0.05:
            freqs.append(f)
        j += 1
    if len(freqs) < 6:
        return None
    twiddles = []
    for f in freqs:
        w = -2.0 * math.pi * f / fs
        twiddles.append(([math.cos(w * i) for i in range(n)], [math.sin(w * i) for i in range(n)]))
    best = 0.0
    for g in groups:
        # lineer interpolasyon ile duzgun orneklem
        series = []
        idx = 0
        for tt in times:
            while idx + 1 < len(items) and items[idx + 1].t < tt:
                idx += 1
            a = items[idx]
            b = items[min(idx + 1, len(items) - 1)]
            if b.t == a.t:
                series.append(a.amp[g])
            else:
                w = max(0.0, min(1.0, (tt - a.t) / (b.t - a.t)))
                series.append(a.amp[g] * (1.0 - w) + b.amp[g] * w)
        # lineer trend cikar
        xm = (n - 1) / 2.0
        ym = _mean(series)
        sxx = sum((i - xm) ** 2 for i in range(n)) or 1.0
        slope = sum((i - xm) * (series[i] - ym) for i in range(n)) / sxx
        series = [(series[i] - ym - slope * (i - xm)) * hann[i] for i in range(n)]
        powers = []
        for cos_t, sin_t in twiddles:
            re = sum(v * cw for v, cw in zip(series, cos_t))
            im = sum(v * sw for v, sw in zip(series, sin_t))
            powers.append(re * re + im * im)
        noise = _median(powers) or 1e-12
        band = [p for f, p in zip(freqs, powers) if band_lo <= f <= band_hi]
        if not band:
            continue
        best = max(best, max(band) / noise)
    return best


# ---------------------------------------------------------------------------
# Histerezisli karar
# ---------------------------------------------------------------------------

class Hysteresis:
    """Giris: son N kararin en az K'si skor>=1. Cikis: skor<release ardisik off_count kez."""

    def __init__(self, on_k=3, on_n=4, off_count=4, release=0.9):
        self.configure(on_k, on_n, off_count, release)
        self.active = False
        self.below = 0
        self.since = None

    def configure(self, on_k, on_n, off_count, release):
        self.on_k = int(on_k)
        self.on_n = max(1, int(on_n))
        self.off_count = int(off_count)
        self.release = float(release)
        old = list(getattr(self, "hist", []))
        self.hist = collections.deque(old[-self.on_n:], maxlen=self.on_n)

    def update(self, score, t=None):
        if score is None:
            return self.active, False
        self.hist.append(score >= 1.0)
        if not self.active:
            if sum(self.hist) >= self.on_k:
                self.active = True
                self.below = 0
                self.since = t
                return True, True
            return False, False
        if score < self.release:
            self.below += 1
        else:
            self.below = 0
        if self.below >= self.off_count:
            self.active = False
            self.hist.clear()
            self.since = None
            return False, True
        return True, False

    def reset(self):
        self.active = False
        self.below = 0
        self.since = None
        self.hist.clear()


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------

ALARM_TEXT = {
    "motion": "Hareket algılandı",
    "static_change": "Ortamda kalıcı değişim",
    "micro_motion": "Mikro-hareket (hareketsiz canlı?)",
    "signal_loss": "CSI sinyali kayboldu",
    "rssi_drop": "RSSI düştü (anten kapatma/sabotaj?)",
}


class SensingEngine:
    def __init__(self, config=None, calibration=None, profiles=None, meta=None):
        import threading
        self.lock = threading.RLock()
        self.config = normalize_config(config)[0]
        self.meta = dict(meta or {})
        self.calibration = calibration if calibration and calibration.get("version") == ENGINE_VERSION else None
        self.profiles = dict(profiles or {})
        self.layouts = {}
        self.bank = ModeBank(self.config["modeMatch"], self.config["maxModes"])
        self.frames = collections.deque()
        self.arrivals = collections.deque()
        self.accepted = collections.deque()
        self.counters = collections.Counter()
        self.nfft = None
        self.next_eval = None
        self.next_micro = None
        self.last_t = None
        self.first_t = None
        self.det = {
            "motion": Hysteresis(),
            "static": Hysteresis(),
            "micro": Hysteresis(),
        }
        self.state_votes = collections.deque()
        self.state_stable = None
        self.state_alarm_label = None
        self.signal_low_since = None
        self.signal_lost = False
        self.signal_seen_ok = False
        self.rebaselined = False
        self.rssi_low_since = None
        self.rssi_dropped = False
        self.quiet_since = None
        self.static_alarm_quiet_since = None
        self.last_alarm_at = {}
        self.cal_session = None
        self.rec_session = None
        self.last_motion = None
        self.last_micro = None
        self.adapted_s = 0.0
        self.refs = self._copy_refs()
        self.residual = None
        self.last_seq = None
        self._make_residual()
        self._configure_detectors()
        self.last_info = self._info(time.time(), {}, {})

    # ------------------------------------------------------------ config/state
    def _configure_detectors(self):
        c = self.config
        rel = c["releaseRatio"]
        self.det["motion"].configure(c["motionOnK"], c["motionOnN"], c["motionOffCount"], rel)
        self.det["static"].configure(c["staticOnK"], c["staticOnN"], c["staticOffCount"], rel)
        self.det["micro"].configure(c["microOnK"], c["microOnN"], 3, rel)
        self.bank.match = c["modeMatch"]
        self.bank.max_modes = c["maxModes"]
        self.fc_allowed = resolve_fc_filter(c["fcFilter"], c["txBackend"])
        if getattr(self, "residual", None) is not None:
            self.residual.configure(c["residualWindowS"], c["residualBaseS"], c["residualTauS"], c["residualBaseS"])

    def _make_residual(self, prime_frames=None):
        model = (self.calibration or {}).get("residual")
        if csi_residual is None or not csi_residual.AVAILABLE or not model:
            self.residual = None
            return
        c = self.config
        self.residual = csi_residual.ResidualDetector(model, c["residualWindowS"], c["residualBaseS"],
                                                     c["residualTauS"], c["residualBaseS"])
        for f in prime_frames or []:
            self.residual.push(f.t, f.rssi, f.cvec, f.mode)

    def _fit_residual(self, frames):
        """Kalibrasyon karelerinden sizinti modeli + ayni kareler uzerinde C(t) dagilimi (esik icin)."""
        c = self.config
        if csi_residual is None or not csi_residual.AVAILABLE or not c["residualEnabled"]:
            return None, []
        model = csi_residual.fit_model(frames)
        if not model:
            return None, []
        det = csi_residual.ResidualDetector(model, c["residualWindowS"], c["residualBaseS"],
                                            c["residualTauS"], c["residualBaseS"])
        vals = []
        next_t = None
        for f in frames:
            det.push(f.t, f.rssi, f.cvec, f.mode)
            if next_t is None:
                next_t = f.t + c["hopS"]
            while f.t >= next_t:
                v = det.statistic(next_t)
                if v is not None:
                    vals.append(v)
                next_t += c["hopS"]
        return model, vals

    def _copy_refs(self):
        if not self.calibration:
            return []
        return [dict(ref, amp=list(ref["amp"]), phase=list(ref["phase"])) for ref in self.calibration.get("references", [])]

    def update_config(self, config):
        with self.lock:
            old = self.config
            self.config = normalize_config(config, base=old)[0]
            if self.config["toneGroups"] != old["toneGroups"]:
                self.layouts.clear()
                self.frames.clear()
                self.bank = ModeBank(self.config["modeMatch"], self.config["maxModes"])
            self._configure_detectors()
            return self.config

    def set_armed(self, armed):
        with self.lock:
            self.config["armed"] = bool(armed)

    def calibration_issues(self):
        """Kalibrasyonun mevcut ayar/donanimla uyumsuzluklari."""
        issues = []
        cal = self.calibration
        if not cal:
            return issues
        snap = cal.get("config") or {}
        for key in CALIBRATION_SENSITIVE_KEYS:
            if key in snap and snap[key] != self.config.get(key):
                issues.append(f"{key}: kalibrasyon {snap[key]} / şimdi {self.config.get(key)}")
        cmeta = cal.get("meta") or {}
        for key in ("channel", "txMac", "backend"):
            if cmeta.get(key) and self.meta.get(key) and cmeta[key] != self.meta[key]:
                issues.append(f"{key}: kalibrasyon {cmeta[key]} / şimdi {self.meta[key]}")
        if cal.get("nfft") and self.nfft and cal["nfft"] != self.nfft:
            issues.append(f"nfft: kalibrasyon {cal['nfft']} / şimdi {self.nfft}")
        return issues

    @property
    def calibrated(self):
        if not self.calibration or not self.refs:
            return False
        snap = self.calibration.get("config") or {}
        if snap.get("toneGroups") != self.config["toneGroups"]:
            return False
        if self.calibration.get("nfft") and self.nfft and self.calibration["nfft"] != self.nfft:
            return False
        # farkli TX yontemi = farkli cerceve/guc/PHY: esikler gecersiz
        cb = (self.calibration.get("meta") or {}).get("backend")
        if cb and self.meta.get("backend") and cb != self.meta["backend"]:
            return False
        return True

    # ------------------------------------------------------------ commands
    def start_calibration(self, duration_s=None, t=None):
        with self.lock:
            duration = float(duration_s or self.config["calibrationS"])
            self.cal_session = {
                "start": t if t is not None else (self.last_t or time.time()),
                "duration": max(5.0, duration),
                "frames": [],
                "motion": [],
                "novelty": [],
                "gain": [],
                "power": [],
                "micro": [],
                "rssi": [],
                "arrivals": 0,
                "acceptedCount": 0,
            }
            self.rec_session = None
            return True

    def cancel_session(self):
        with self.lock:
            self.cal_session = None
            self.rec_session = None

    def start_profile(self, label, duration_s=30.0, t=None):
        label = "".join(ch for ch in str(label or "").strip().lower().replace(" ", "_")
                        if ch.isalnum() or ch in "_-")
        if not label:
            return False
        with self.lock:
            self.rec_session = {
                "label": label,
                "start": t if t is not None else (self.last_t or time.time()),
                "duration": max(5.0, float(duration_s or 30.0)),
                "frames": [],
                "motion": [],
            }
            self.cal_session = None
            return True

    def delete_profile(self, label):
        with self.lock:
            return self.profiles.pop(label, None) is not None

    def accept_baseline(self):
        """Mevcut statik durumu yeni normal kabul et (referans ortalamalari guncellenir)."""
        with self.lock:
            if not self.refs:
                return False
            window = [f for f in self.frames if f.t >= (self.last_t or 0) - self.config["staticWindowS"]]
            current = significant_modes(mode_means(window))
            changed = False
            used = set()
            # bire-bir eslestirme, buyuk moddan kucuge
            for cur in sorted(current.values(), key=lambda c: -c["n"]):
                candidates = [r for r in self.refs if id(r) not in used]
                ref, _ = match_reference(cur["unit"], candidates, self.config["modeMatch"] - 0.05)
                if ref is None:
                    # degisimden sonra hicbir referansa benzemeyen sekil: yeni referans olarak ekle,
                    # sapmalari en yakin referanstan devral
                    nearest = max(self.refs, key=lambda r: _dot(cur["unit"], r["unit"]))
                    ref = {"ampSd": list(nearest["ampSd"]), "phaseSd": list(nearest["phaseSd"]),
                           "frames": cur["n"], "share": 0.0, "chunkDistances": []}
                    self.refs.append(ref)
                ref["amp"] = list(cur["amp"])
                ref["phase"] = list(cur["phase"])
                ref["unit"] = list(cur["unit"])
                used.add(id(ref))
                changed = True
            if changed:
                self.det["static"].reset()
                self.static_alarm_quiet_since = None
            return changed

    def _enroll_new_modes(self, current_modes):
        """Bilinen modlar normalken surekli gorulen esi olmayan PHY modunu referansa ekler."""
        if len(self.refs) >= self.config["maxModes"] + 2:
            return
        for cur in current_modes.values():
            if cur["n"] < 12:
                continue
            ref, _ = match_reference(cur["unit"], self.refs, self.config["modeMatch"] - 0.05)
            if ref is not None:
                continue
            nearest = max(self.refs, key=lambda r: _dot(cur["unit"], r["unit"]))
            self.refs.append({"unit": list(cur["unit"]), "amp": list(cur["amp"]), "phase": list(cur["phase"]),
                              "ampSd": list(nearest["ampSd"]), "phaseSd": list(nearest["phaseSd"]),
                              "frames": cur["n"], "share": 0.0, "chunkDistances": [], "enrolled": True})
            self.counters["enrolledModes"] += 1

    # ------------------------------------------------------------ ingest
    def _layout(self, nfft):
        layout = self.layouts.get(nfft)
        if layout is None and nfft in _TONE_RANGE:
            layout = ToneLayout(nfft, self.config["toneGroups"])
            self.layouts[nfft] = layout
        return layout

    def push(self, sample):
        """Bir Nexmon CSI ornegi isler.

        sample: {"ts": ms, "nfft": int, "iq": bytes, "rssi": int, "frameControl": int}
        Donus: (info | None, events)  - info yalnizca karar anlarinda doner.
        """
        t = float(sample.get("ts") or 0) / 1000.0
        with self.lock:
            events = []
            if self.last_t is not None and t < self.last_t - 5.0:
                # saat geri gitti (replay yeni dosya vb.): zaman cizelgesini sifirla
                self.next_eval = None
                self.first_t = None
                self.frames.clear()
                self.arrivals.clear()
                self.accepted.clear()
            info = self._advance(t, events)
            self.last_t = t if self.last_t is None else max(self.last_t, t)
            if self.first_t is None:
                self.first_t = t
            self.arrivals.append(t)
            self.counters["total"] += 1
            if self.cal_session is not None:
                self.cal_session["arrivals"] += 1
            fc_allowed = self.fc_allowed
            fc = sample.get("frameControl")
            if fc_allowed and fc is not None and int(fc) not in fc_allowed:
                self.counters["rejectedFc"] += 1
                return info, events
            # Yeniden gonderim (ayni sira-kontrol alani art arda): verici farkli anten/ayarla tekrar
            # yollar; artigi bozar (2026-11-09: atilmazsa bos odada 29, atilinca 2 yanlis kume).
            # BlockAck'te sira no yok (0xFFFF) -> dokunulmaz.
            seq = sample.get("seq")
            if seq is not None and seq != 0xFFFF:
                if seq == self.last_seq:
                    self.counters["rejected_retry"] += 1
                    return info, events
                self.last_seq = seq
            nfft = int(sample.get("nfft") or 0)
            layout = self._layout(nfft)
            if layout is None:
                self.counters["rejectedNfft"] += 1
                return info, events
            frame, reason = extract_frame(sample.get("iq"), nfft, layout, t, sample.get("rssi") or 0)
            if frame is None:
                self.counters["rejected_" + reason] += 1
                return info, events
            if self.nfft != nfft:
                self.nfft = nfft
            frame.mode, _corr, frame.novel = self.bank.assign(frame.unit)
            self.counters["accepted"] += 1
            self.frames.append(frame)
            if self.residual is not None:
                self.residual.push(t, frame.rssi, frame.cvec, frame.mode)
            self.accepted.append(t)
            if self.cal_session is not None:
                self.cal_session["frames"].append(frame)
                self.cal_session["acceptedCount"] += 1
            if self.rec_session is not None:
                self.rec_session["frames"].append(frame)
            return info, events

    def tick(self, now=None):
        """Kare gelmese de zamanin ilerlemesi (sinyal kaybi tespiti) icin periyodik cagri."""
        with self.lock:
            events = []
            now = time.time() if now is None else float(now)
            if self.last_t is None:
                return None, events
            info = self._advance(now, events)
            return info, events

    def _advance(self, t, events):
        hop = self.config["hopS"]
        if self.next_eval is None:
            self.next_eval = t + hop
            return None
        info = None
        steps = 0
        while t >= self.next_eval and steps < 8:
            info = self._evaluate(self.next_eval, events)
            self.next_eval += hop
            steps += 1
        if t >= self.next_eval:
            # buyuk bosluk: yetismeye calisma, son ana atla
            info = self._evaluate(t, events)
            self.next_eval = t + hop
        return info

    # ------------------------------------------------------------ evaluation
    def _prune(self, t):
        c = self.config
        keep = max(c["motionWindowS"], c["staticWindowS"],
                   c["microWindowS"] if c["microEnabled"] else 0.0, 4.0) + 1.0
        while self.frames and self.frames[0].t < t - keep:
            self.frames.popleft()
        while self.arrivals and self.arrivals[0] < t - 2.0:
            self.arrivals.popleft()
        while self.accepted and self.accepted[0] < t - 2.0:
            self.accepted.popleft()

    def warming_up(self, t):
        c = self.config
        if self.first_t is None:
            return True
        warm = max(c["motionWindowS"], c["staticWindowS"]) + 1.0
        return t - self.first_t < warm

    def _evaluate(self, t, events):
        c = self.config
        self._prune(t)
        warming = self.warming_up(t)
        rate = len(self.accepted) / 2.0
        raw_rate = len(self.arrivals) / 2.0
        motion_frames = [f for f in self.frames if f.t > t - c["motionWindowS"] and f.t <= t]
        static_frames = [f for f in self.frames if f.t > t - c["staticWindowS"] and f.t <= t]
        metrics = {
            "rate": rate,
            "rawRate": raw_rate,
        }
        motion = within_mode_motion(motion_frames)
        power = within_mode_power(motion_frames) if c["motionPowerEnabled"] or self.cal_session is not None else None
        metrics["power"] = power
        novelty = (sum(1 for f in motion_frames if f.novel) / len(motion_frames)) if len(motion_frames) >= 5 else None
        metrics["motion"] = motion
        metrics["novelty"] = novelty
        metrics["gain"] = gain_state_share(motion_frames, c["gainStateTolDb"], c["gainStateMaxDb"])
        metrics["residual"] = (self.residual.statistic(t)
                               if self.residual is not None and c["residualEnabled"] else None)
        rssi_values = [f.rssi for f in static_frames]
        metrics["rssi"] = _median(rssi_values) if rssi_values else None
        current_modes = significant_modes(mode_means(static_frames))
        metrics["modesInWindow"] = len(current_modes)
        if static_frames:
            counts = collections.Counter(f.mode for f in static_frames)
            metrics["primaryShare"] = counts.most_common(1)[0][1] / len(static_frames)
        # mikro (daha seyrek)
        micro = None
        if c["microEnabled"] and (self.next_micro is None or t >= self.next_micro):
            self.next_micro = t + max(1.0, c["hopS"])
            micro_frames = [f for f in self.frames if f.t > t - c["microWindowS"] and f.t <= t]
            micro = micro_motion_metric(micro_frames, c["microWindowS"], c["microBandLowHz"], c["microBandHighHz"])
            self.last_micro = micro
        metrics["micro"] = self.last_micro if c["microEnabled"] else None

        metrics["warmingUp"] = warming
        # --- kalibrasyon / profil toplama
        if warming:
            pass
        elif self.cal_session is not None:
            s = self.cal_session
            if motion is not None:
                s["motion"].append(motion)
            if novelty is not None:
                s["novelty"].append(novelty)
            if metrics["gain"] is not None:
                s.setdefault("gain", []).append(metrics["gain"])
            if power is not None:
                s["power"].append(power)
            if micro is not None:
                s["micro"].append(micro)
            if metrics["rssi"] is not None:
                s["rssi"].append(metrics["rssi"])
            if t - s["start"] >= s["duration"]:
                self._finish_calibration(t, events)
        elif self.rec_session is not None:
            s = self.rec_session
            if motion is not None:
                s["motion"].append(motion)
            if t - s["start"] >= s["duration"]:
                self._finish_profile(t, events)

        return self._decide(t, metrics, current_modes, events)

    def _decide(self, t, metrics, current_modes, events):
        c = self.config
        cal = self.calibration if self.calibrated else None
        thr = (cal or {}).get("thresholds") or {}
        detectors = {}
        collecting = (self.cal_session is not None or self.rec_session is not None
                      or bool(metrics.get("warmingUp")))

        # --- sinyal kalitesi
        low = metrics["rate"] < c["minPacketRate"]
        if not low and not metrics.get("warmingUp"):
            self.signal_seen_ok = True
        if low and self.signal_seen_ok:
            if self.signal_low_since is None:
                self.signal_low_since = t
        elif not low:
            self.signal_low_since = None
        was_lost = self.signal_lost
        self.signal_lost = bool(self.signal_low_since is not None and t - self.signal_low_since >= c["signalLossS"])
        if c["signalLossEnabled"] and self.signal_lost and not was_lost:
            self._alarm(events, t, "signal_loss", 1.0, {"rate": metrics["rate"]})
        if was_lost and not self.signal_lost:
            events.append({"kind": "clear", "label": "signal_loss", "t": t})
        signal_ok = not low

        # --- hareket
        m_thr = thr.get("motion")
        n_thr = thr.get("novelty")
        motion_score = None
        if cal and signal_ok and metrics["motion"] is not None and m_thr:
            motion_score = metrics["motion"] / (m_thr * c["motionScale"])
            if c["noveltyEnabled"] and metrics["novelty"] is not None and n_thr:
                motion_score = max(motion_score, metrics["novelty"] / (n_thr * c["motionScale"]))
            g_thr = thr.get("gain") or c["gainMinShare"]
            # G yalniz ax210_ba'da anlamli (AP baglantisinin BlockAck guc durumlarina etkisi). ax210_action'da
            # olaylara tepki vermedi, kendiliginden durum gecislerinde yanlis alarm uretti (2026-11-09 §11.6).
            gain_ok = c["gainStateEnabled"] and c.get("txBackend") == "ax210_ba"
            if gain_ok and metrics.get("gain") is not None and g_thr:
                g_thr = max(g_thr, c["gainMinShare"])
                motion_score = max(motion_score, metrics["gain"] / (g_thr * c["motionScale"]))
            r_thr = thr.get("residual")
            if c["residualEnabled"] and metrics.get("residual") is not None and r_thr:
                r_thr = max(r_thr, c["residualMinThr"])
                motion_score = max(motion_score, metrics["residual"] / (r_thr * c["motionScale"]))
            p_thr = thr.get("power")
            if c["motionPowerEnabled"] and metrics.get("power") is not None and p_thr:
                motion_score = max(motion_score, metrics["power"] / (p_thr * c["motionScale"]))
        m_active, m_changed = (False, False)
        if c["motionEnabled"] and not collecting:
            m_active, m_changed = self.det["motion"].update(motion_score, t)
            if m_changed and m_active:
                self._alarm(events, t, "motion", motion_score, {"motion": metrics["motion"], "novelty": metrics["novelty"],
                                                               "gain": metrics.get("gain"),
                                                               "residual": metrics.get("residual")})
            elif m_changed:
                events.append({"kind": "clear", "label": "motion", "t": t})
        elif not c["motionEnabled"]:
            self.det["motion"].reset()
        detectors["motion"] = {
            "enabled": c["motionEnabled"],
            "value": metrics["motion"],
            "novelty": metrics["novelty"],
            "power": metrics.get("power"),
            "thr": (m_thr * c["motionScale"]) if m_thr else None,
            "noveltyThr": (n_thr * c["motionScale"]) if n_thr else None,
            "residual": metrics.get("residual"),
            "residualThr": (max(thr.get("residual") or 0.0, c["residualMinThr"]) * c["motionScale"])
                           if cal and c["residualEnabled"] and thr.get("residual") else None,
            "gain": metrics.get("gain"),
            "gainThr": (max(thr.get("gain") or 0.0, c["gainMinShare"]) * c["motionScale"])
                       if cal and c["gainStateEnabled"] and c.get("txBackend") == "ax210_ba" else None,
            "score": motion_score,
            "active": self.det["motion"].active,
        }
        # "sessiz" = hareket dedektoru aktif degil ve anlik skor esigin altinda
        moving = self.det["motion"].active or (motion_score is not None and motion_score >= 1.0)
        if moving or not signal_ok or motion_score is None:
            self.quiet_since = None
        elif self.quiet_since is None:
            self.quiet_since = t
        quiet_for = (t - self.quiet_since) if self.quiet_since is not None else 0.0

        # --- statik degisim
        s_thr = thr.get("static")
        static_dist = None
        unmatched = 0.0
        static_score = None
        evaluated = False
        if cal and signal_ok and current_modes:
            static_dist, unmatched, matches = weighted_distance(
                current_modes, self.refs, c["phaseWeight"], c["modeMatch"] - 0.05)
            if static_dist is None and unmatched > 0.5:
                # bilinen hicbir moda benzemiyor: ya buyuk ortam degisimi ya da tamamen yeni PHY modu
                static_score = 3.0
            elif static_dist is not None and s_thr:
                # Bilinen mod(lar) varsa karar onlarla verilir: ortam degisimi TUM modlari etkiler,
                # yalniz yeni bir modun belirmesi (hiz adaptasyonu) ortam degisimi sayilmaz.
                eff = max(s_thr, c["staticMinDist"]) * c["staticScale"]
                static_score = static_dist / eff
            evaluated = quiet_for >= c["staticQuietS"]
            if (evaluated and not self.rebaselined and c["rebaselineOnStart"] and not collecting
                    and not self.det["static"].active):
                if self.accept_baseline():
                    self.rebaselined = True
                    events.append({"kind": "info", "label": "baseline_armed", "t": t})
                    static_dist, unmatched, matches = weighted_distance(
                        current_modes, self.refs, c["phaseWeight"], c["modeMatch"] - 0.05)
                    if static_dist is not None and s_thr:
                        static_score = static_dist / (max(s_thr, c["staticMinDist"]) * c["staticScale"])
            # yavas drift takibi (yalniz sessiz ve normalken)
            if (evaluated and c["staticAdaptTauS"] > 0 and static_score is not None
                    and static_score < 0.6 and not self.det["static"].active and not collecting):
                alpha = min(1.0, c["hopS"] / c["staticAdaptTauS"])
                for ref, cur, _d in matches:
                    ref["amp"] = [(1 - alpha) * a + alpha * b for a, b in zip(ref["amp"], cur["amp"])]
                    ref["phase"] = [_wrap(a + alpha * _wrap(b - a)) for a, b in zip(ref["phase"], cur["phase"])]
                self.adapted_s += c["hopS"]
                self._enroll_new_modes(current_modes)
        if c["staticEnabled"] and not collecting:
            if evaluated:
                s_active, s_changed = self.det["static"].update(static_score, t)
                if s_changed and s_active:
                    self._alarm(events, t, "static_change", static_score,
                                {"distance": static_dist, "unmatchedShare": unmatched})
                elif s_changed:
                    events.append({"kind": "clear", "label": "static_change", "t": t})
            # otomatik yeni referans
            if self.det["static"].active and c["staticAutoRelearnS"] > 0:
                if evaluated:
                    if self.static_alarm_quiet_since is None:
                        self.static_alarm_quiet_since = t
                    elif t - self.static_alarm_quiet_since >= c["staticAutoRelearnS"]:
                        if self.accept_baseline():
                            events.append({"kind": "info", "label": "baseline_relearned", "t": t})
                else:
                    self.static_alarm_quiet_since = None
        elif not c["staticEnabled"]:
            self.det["static"].reset()
        detectors["static"] = {
            "enabled": c["staticEnabled"],
            "value": static_dist,
            "thr": (max(s_thr, c["staticMinDist"]) * c["staticScale"]) if s_thr else None,
            "score": static_score,
            "active": self.det["static"].active,
            "evaluated": evaluated,
            "unmatchedShare": unmatched,
            "quietS": round(quiet_for, 1),
            "adaptedS": round(self.adapted_s, 1),
        }

        # --- durum profilleri
        detectors["state"] = self._classify_state(t, current_modes, signal_ok, collecting, events)

        # --- mikro
        mi_thr = thr.get("micro")
        micro_score = None
        if (c["microEnabled"] and cal and signal_ok and metrics["micro"] is not None and mi_thr
                and quiet_for >= c["microWindowS"]):
            micro_score = metrics["micro"] / (mi_thr * c["microScale"])
        if c["microEnabled"] and not collecting:
            mi_active, mi_changed = self.det["micro"].update(micro_score, t)
            if mi_changed and mi_active:
                self._alarm(events, t, "micro_motion", micro_score, {"micro": metrics["micro"]})
            elif mi_changed:
                events.append({"kind": "clear", "label": "micro_motion", "t": t})
            if moving and self.det["micro"].active:
                self.det["micro"].reset()
        elif not c["microEnabled"]:
            self.det["micro"].reset()
        detectors["micro"] = {
            "enabled": c["microEnabled"],
            "value": metrics["micro"],
            "thr": (mi_thr * c["microScale"]) if mi_thr else None,
            "score": micro_score,
            "active": self.det["micro"].active,
        }

        # --- RSSI dususu
        base_rssi = (cal or {}).get("rssi")
        drop = None
        if base_rssi is not None and metrics["rssi"] is not None:
            drop = base_rssi - metrics["rssi"]
        dropped_now = drop is not None and drop >= c["rssiDropDb"]
        if dropped_now:
            if self.rssi_low_since is None:
                self.rssi_low_since = t
        else:
            self.rssi_low_since = None
        was = self.rssi_dropped
        self.rssi_dropped = bool(self.rssi_low_since is not None and t - self.rssi_low_since >= c["rssiHoldS"])
        if c["rssiDropEnabled"] and self.rssi_dropped and not was and not collecting:
            self._alarm(events, t, "rssi_drop", drop / c["rssiDropDb"], {"dropDb": drop})
        detectors["rssi"] = {
            "enabled": c["rssiDropEnabled"],
            "value": metrics["rssi"],
            "baseline": base_rssi,
            "dropDb": drop,
            "active": self.rssi_dropped and c["rssiDropEnabled"],
        }
        detectors["signal"] = {
            "enabled": c["signalLossEnabled"],
            "rate": metrics["rate"],
            "rawRate": metrics["rawRate"],
            "lost": self.signal_lost,
            "active": self.signal_lost and c["signalLossEnabled"],
        }
        info = self._info(t, metrics, detectors)
        self.last_info = info
        return info

    def _classify_state(self, t, current_modes, signal_ok, collecting, events):
        c = self.config
        out = {"enabled": c["stateEnabled"], "profiles": sorted(self.profiles.keys()),
               "label": None, "stable": self.state_stable, "distances": {}, "unknown": False,
               "active": False, "alarmLabels": parse_label_list(c["stateAlarmLabels"])}
        if not c["stateEnabled"] or not self.profiles or not current_modes or not signal_ok or collecting:
            out["active"] = self.state_alarm_label is not None
            return out
        scored = []
        for label, prof in self.profiles.items():
            if prof.get("toneGroups") != c["toneGroups"]:
                continue
            d, unmatched, _ = weighted_distance(current_modes, prof.get("references") or [],
                                                c["phaseWeight"], c["modeMatch"] - 0.05)
            if d is None:
                continue
            p99 = max(1.5, float(prof.get("distP99") or 1.0))
            scored.append((d / p99, d, label))
            out["distances"][label] = round(d, 3)
        decision = None
        if scored:
            scored.sort()
            rel, d, label = scored[0]
            if rel > c["openSetFactor"]:
                decision = "unknown"
                out["unknown"] = True
            elif len(scored) > 1:
                rel2 = scored[1][0]
                margin = (rel2 - rel) / max(1e-9, rel2 + rel)
                decision = label if margin >= c["stateMargin"] else "uncertain"
            else:
                decision = label
        out["label"] = decision
        # K-of-N kararlilik
        self.state_votes.append(decision)
        while len(self.state_votes) > c["stateOnN"]:
            self.state_votes.popleft()
        counts = collections.Counter(v for v in self.state_votes if v not in (None, "uncertain"))
        if counts:
            top, n = counts.most_common(1)[0]
            if n >= c["stateOnK"] and top != self.state_stable:
                previous = self.state_stable
                self.state_stable = top
                events.append({"kind": "state", "label": top, "previous": previous, "t": t})
                alarm_labels = parse_label_list(c["stateAlarmLabels"])
                if top in alarm_labels:
                    self.state_alarm_label = top
                    self._alarm(events, t, "state_" + top, 1.0, {"distances": out["distances"], "previous": previous})
                elif self.state_alarm_label is not None:
                    events.append({"kind": "clear", "label": "state_" + self.state_alarm_label, "t": t})
                    self.state_alarm_label = None
        out["stable"] = self.state_stable
        out["active"] = self.state_alarm_label is not None
        return out

    def _alarm(self, events, t, label, score, detail):
        c = self.config
        last = self.last_alarm_at.get(label)
        suppressed = None
        if not c["armed"]:
            suppressed = "disarmed"
        elif last is not None and t - last < c["alarmCooldownS"]:
            suppressed = "cooldown"
        else:
            self.last_alarm_at[label] = t
        events.append({
            "kind": "alarm",
            "label": label,
            "text": ALARM_TEXT.get(label) or ("Durum: " + label[6:] if label.startswith("state_") else label),
            "t": t,
            "score": round(float(score), 3) if score is not None else None,
            "detail": _json_safe(detail),
            "suppressed": suppressed,
            "scenario": c["scenario"],
            "engine": ENGINE_VERSION,
        })

    # ------------------------------------------------------------ finishing sessions
    def _finish_calibration(self, t, events):
        s = self.cal_session
        self.cal_session = None
        c = self.config
        frames = s["frames"]
        duration = max(1e-6, t - s["start"])
        warnings = []
        refs = build_mode_references(frames, c["staticWindowS"], c["phaseWeight"])
        if len(frames) < 50 or not refs or len(s["motion"]) < 5:
            events.append({"kind": "calibration_failed", "t": t,
                           "reason": f"yetersiz veri: {len(frames)} kare, {len(refs)} referans mod",
                           "frames": len(frames)})
            return
        k = c["calMadK"]
        q = c["calPercentile"]
        margin = c["calMargin"]
        static_d = [d for ref in refs for d in ref.get("chunkDistances", [])]
        thresholds = {
            "motion": robust_threshold(s["motion"], k, q, margin, c["motionMinThr"]),
            "novelty": robust_threshold(s["novelty"], k, q, margin, 0.15) if s["novelty"] else 0.15,
            "gain": robust_threshold(s.get("gain") or [], k, q, margin, c["gainMinShare"]) if s.get("gain") else None,
            "power": robust_threshold(s["power"], k, q, margin, 0.002) if s["power"] else None,
            "static": robust_threshold(static_d, k / 2.0, q, margin, 1.0) if static_d else 1.0,
            "micro": robust_threshold(s["micro"], k / 2.0, q, margin, 4.0) if s["micro"] else None,
        }
        residual_model, residual_vals = self._fit_residual(frames)
        if residual_model and residual_vals:
            thresholds["residual"] = robust_threshold(residual_vals, k, q, margin, c["residualMinThr"])
        rate = s["acceptedCount"] / duration
        accept_ratio = s["acceptedCount"] / max(1, s["arrivals"])
        p50 = _median(s["motion"])
        p99 = _percentile(s["motion"], 99)
        if rate < 10:
            warnings.append(f"Düşük geçerli kare hızı ({rate:.1f}/sn). TX pps'i artırın veya kanal/filtreyi kontrol edin.")
        if accept_ratio < 0.5:
            warnings.append(f"Karelerin yalnızca %{accept_ratio * 100:.0f}'i kabul edildi (frame-control/kalite filtresi).")
        if p50 > 0 and p99 / p50 > 4.0:
            warnings.append("Kalibrasyon sırasında hareket olmuş olabilir (p99/p50 > 4). Ortam boşken tekrarlayın.")
        if s.get("gain") and _percentile(s["gain"], 99) > 0.15:
            warnings.append("Kalibrasyonda kazanç durumu geçişleri sık (p99 > %15): ortamda hareket olmuş olabilir; "
                            "kazanç kanalı bu kalibrasyonla duyarsızlaşır.")
        if len(refs) > 3:
            warnings.append(f"{len(refs)} farklı PHY modu: TX hız adaptasyonu yüksek; sonuçlar yine de mod bazında doğru.")
        calibration = {
            "version": ENGINE_VERSION,
            "createdAt": int(time.time() * 1000),
            "durationS": round(duration, 1),
            "nfft": self.nfft,
            "meta": dict(self.meta),
            "config": {key: c[key] for key in CALIBRATION_SENSITIVE_KEYS + ("scenario", "phaseWeight", "modeMatch")},
            "frames": len(frames),
            "rate": round(rate, 2),
            "acceptRatio": round(accept_ratio, 3),
            "rssi": _median(s["rssi"]) if s["rssi"] else None,
            "thresholds": {key: (round(v, 6) if v is not None else None) for key, v in thresholds.items()},
            "stats": {
                "motionP50": round(p50, 6),
                "motionP99": round(p99, 6),
                "noveltyP99": round(_percentile(s["novelty"], 99), 4) if s["novelty"] else None,
                "gainP99": round(_percentile(s["gain"], 99), 4) if s.get("gain") else None,
                "residualP50": round(_percentile(residual_vals, 50), 3) if residual_vals else None,
                "residualP99": round(_percentile(residual_vals, 99), 3) if residual_vals else None,
                "staticP99": round(_percentile(static_d, 99), 4) if static_d else None,
                "microP99": round(_percentile(s["micro"], 99), 3) if s["micro"] else None,
                "evaluations": len(s["motion"]),
            },
            "references": [_serialize_ref(ref) for ref in refs],
            "warnings": warnings,
        }
        if residual_model and residual_vals:
            calibration["residual"] = residual_model
        self.calibration = calibration
        self.refs = self._copy_refs()
        keep = c["residualWindowS"] + c["residualBaseS"] + 1.0
        self._make_residual([f for f in frames if f.t >= t - keep])
        self.adapted_s = 0.0
        for det in self.det.values():
            det.reset()
        events.append({"kind": "calibration_done", "t": t, "calibration": calibration})

    def _finish_profile(self, t, events):
        s = self.rec_session
        self.rec_session = None
        c = self.config
        refs = build_mode_references(s["frames"], c["staticWindowS"], c["phaseWeight"])
        if not refs:
            events.append({"kind": "profile_failed", "label": s["label"], "t": t,
                           "reason": f"yetersiz veri ({len(s['frames'])} kare)"})
            return
        dists = [d for ref in refs for d in ref.get("chunkDistances", [])]
        warnings = []
        cal_thr = ((self.calibration or {}).get("thresholds") or {}).get("motion")
        if cal_thr and s["motion"] and _percentile(s["motion"], 90) > cal_thr:
            warnings.append("Kayıt sırasında hareket algılandı; profil gürültülü olabilir.")
        profile = {
            "label": s["label"],
            "createdAt": int(time.time() * 1000),
            "durationS": round(t - s["start"], 1),
            "frames": len(s["frames"]),
            "nfft": self.nfft,
            "toneGroups": c["toneGroups"],
            "distP99": round(max(1.0, _percentile(dists, 99) if dists else 1.0), 4),
            "references": [_serialize_ref(ref) for ref in refs],
            "warnings": warnings,
            "meta": dict(self.meta),
        }
        self.profiles[s["label"]] = profile
        self.state_votes.clear()
        events.append({"kind": "profile_done", "label": s["label"], "t": t, "profile": profile})

    # ------------------------------------------------------------ reporting
    def _overall(self, detectors):
        if self.cal_session is not None:
            return "calibrating"
        if self.rec_session is not None:
            return "recording"
        if self.signal_lost or not self.signal_seen_ok:
            return "no_signal"
        if not self.calibrated:
            return "uncalibrated"
        if any((detectors.get(name) or {}).get("active") for name in ("motion", "static", "micro", "rssi", "state")):
            return "alarm"
        return "normal"

    def _info(self, t, metrics, detectors):
        c = self.config
        cal = self.calibration
        session = None
        if self.cal_session is not None:
            s = self.cal_session
            elapsed = max(0.0, t - s["start"])
            session = {"kind": "calibration", "progress": min(1.0, elapsed / s["duration"]),
                       "remainingS": round(max(0.0, s["duration"] - elapsed), 1),
                       "durationS": s["duration"], "frames": len(s["frames"])}
        elif self.rec_session is not None:
            s = self.rec_session
            elapsed = max(0.0, t - s["start"])
            session = {"kind": "profile", "label": s["label"], "progress": min(1.0, elapsed / s["duration"]),
                       "remainingS": round(max(0.0, s["duration"] - elapsed), 1),
                       "durationS": s["duration"], "frames": len(s["frames"])}
        active = [name for name in ("motion", "static", "micro", "rssi", "signal")
                  if (detectors.get(name) or {}).get("active")]
        if self.state_alarm_label:
            active.append("state_" + self.state_alarm_label)
        return _json_safe({
            "engine": ENGINE_VERSION,
            "t": round(t, 3),
            "overall": self._overall(detectors),
            "armed": c["armed"],
            "scenario": c["scenario"],
            "calibrated": self.calibrated,
            "calibration": {
                "createdAt": cal.get("createdAt"),
                "durationS": cal.get("durationS"),
                "frames": cal.get("frames"),
                "rate": cal.get("rate"),
                "modes": len(cal.get("references") or []),
                "thresholds": cal.get("thresholds"),
                "warnings": cal.get("warnings") or [],
                "issues": self.calibration_issues(),
            } if cal else None,
            "session": session,
            "signal": {
                "rate": metrics.get("rate"),
                "rawRate": metrics.get("rawRate"),
                "rssi": metrics.get("rssi"),
                "nfft": self.nfft,
                "modes": self.bank.summary()[:6],
                "modesInWindow": metrics.get("modesInWindow"),
                "primaryShare": metrics.get("primaryShare"),
                "counters": dict(self.counters),
            },
            "detectors": detectors,
            "activeAlarms": active,
        })

    def status(self):
        with self.lock:
            return dict(self.last_info)

    def last_frame(self):
        with self.lock:
            return self.frames[-1] if self.frames else None

    def profiles_summary(self):
        with self.lock:
            return {
                label: {
                    "createdAt": p.get("createdAt"),
                    "durationS": p.get("durationS"),
                    "frames": p.get("frames"),
                    "modes": len(p.get("references") or []),
                    "distP99": p.get("distP99"),
                    "warnings": p.get("warnings") or [],
                }
                for label, p in self.profiles.items()
            }


def _serialize_ref(ref):
    return {
        "unit": _round_list(ref["unit"]),
        "amp": _round_list(ref["amp"]),
        "ampSd": _round_list(ref["ampSd"]),
        "phase": _round_list(ref["phase"]),
        "phaseSd": _round_list(ref["phaseSd"]),
        "frames": ref["frames"],
        "share": round(ref["share"], 4),
        "chunkDistances": _round_list(ref.get("chunkDistances") or [], 4),
    }


def _json_safe(value):
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return round(value, 6)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# Nexmon pcap okuma (offline replay ve testler icin; canli servis kendi
# ayristiricisini kullanir ama ayni alanlari uretir)
# ---------------------------------------------------------------------------

def parse_nexmon_frame(frame, ts_sec, ts_usec):
    if len(frame) < 42 or struct.unpack("!H", frame[12:14])[0] != 0x0800:
        return None
    ihl = (frame[14] & 0x0F) * 4
    if ihl < 20 or frame[14 + 9] != 17:
        return None
    udp = 14 + ihl
    if len(frame) < udp + 8 or struct.unpack("!H", frame[udp + 2:udp + 4])[0] != 5500:
        return None
    payload = frame[udp + 8:]
    if len(payload) < 18 or payload[0:2] != b"\x11\x11":
        return None
    csi = payload[18:]
    nfft = nfft_from_payload_len(len(csi))
    if nfft <= 0:
        return None
    return {
        "ts": ts_sec * 1000 + ts_usec / 1000.0,
        "rssi": struct.unpack("b", payload[2:3])[0],
        "frameControl": payload[3],
        "sourceMac": ":".join(f"{b:02x}" for b in payload[4:10]),
        "seq": struct.unpack("<H", payload[10:12])[0],
        "chanspec": struct.unpack("<H", payload[14:16])[0],
        "nfft": nfft,
        "iq": bytes(csi[:nfft * 4]),
    }


def iter_pcap(path, start_s=None, end_s=None):
    """pcap -> Nexmon ornekleri. start_s/end_s: dosya basina gore saniye (opsiyonel)."""
    with open(path, "rb") as handle:
        header = handle.read(24)
        if len(header) != 24:
            return
        magic = header[:4]
        if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
            endian = "<"
        elif magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
            endian = ">"
        else:
            raise ValueError(f"unexpected pcap magic {magic.hex()}")
        nano = magic in (b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d")
        first = None
        while True:
            pkt = handle.read(16)
            if len(pkt) != 16:
                break
            ts_sec, ts_frac, incl, _orig = struct.unpack(endian + "IIII", pkt)
            frame = handle.read(incl)
            if len(frame) != incl:
                break
            usec = ts_frac / 1000.0 if nano else ts_frac
            t = ts_sec + usec / 1e6
            if first is None:
                first = t
            rel = t - first
            if start_s is not None and rel < start_s:
                continue
            if end_s is not None and rel >= end_s:
                break
            sample = parse_nexmon_frame(frame, ts_sec, usec)
            if sample:
                yield sample


def write_pcap(path, samples):
    """Sentetik/test amacli: Nexmon UDP formatinda pcap yazar. samples: dict(ts, rssi, frameControl, sourceMac, nfft, iq)."""
    with open(path, "wb") as out:
        out.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        for s in samples:
            mac = bytes(int(x, 16) for x in s.get("sourceMac", "00:00:00:00:00:00").split(":"))
            payload = (b"\x11\x11" + struct.pack("b", int(s.get("rssi", -40))) + bytes([s.get("frameControl", 0x88)])
                       + mac + struct.pack("<HHHH", s.get("seq", 0) & 0xFFFF, 0, 0xE32A, 0x4345) + s["iq"])
            udp = struct.pack("!HHHH", 5500, 5500, 8 + len(payload), 0) + payload
            ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0,
                             b"\x0a\x0a\x0a\x0a", b"\xff\xff\xff\xff") + udp
            eth = b"\xff" * 6 + b"\x02" * 6 + b"\x08\x00" + ip
            ts = float(s["ts"]) / 1000.0
            sec = int(ts)
            usec = int(round((ts - sec) * 1e6))
            if usec >= 1000000:
                sec += 1
                usec -= 1000000
            out.write(struct.pack("<IIII", sec, usec, len(eth), len(eth)))
            out.write(eth)


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default
