import json, numpy as np
from bench import gt_pairs, cal_done

P = lambda s: f'csi-alpha-web-20261109-{s}'
GROUP = {'hat_disi_yakin': 'yakin', 'sandalye': 'yakin', 'sandalye_otur_kalk': 'yakin', 'hareketsiz_insan_ayakta': 'yakin',
         'hat_disi_uzak': 'orta', 'kapi_acildi': 'uzak', 'kapi_kapandi': 'uzak', 'insan_girdi': 'uzak',
         'insan_girdi_cikti': 'uzak', 'kapi_disi_hareket': 'disari', 'bos_oda': 'bos', 'oda_bos': 'bos'}

def session(tag, cal_from=None, cal_s=None):
    ev = P(tag) + '.events.ndjson'
    pairs = gt_pairs(ev)
    ce = cal_done(ev)
    return dict(tag=tag, pcap=P(tag) + '.pcap', pairs=pairs, ce=ce[0] if ce else None, cal_s=cal_s, cal_from=cal_from)

SESS = {
    'dev':  session('171655', cal_s=60),
    'S2':   session('190248', cal_s=60),
    'S3':   session('194918', cal_s=180),
    'S4':   session('202201', cal_from='S3'),
}

