# Projeyi Detaylarıyla Anlamak

*Wi-Fi sinyaliyle, kamera kullanmadan, tek bir küçük bilgisayarın odadaki hareketi nasıl fark ettiğinin
baştan sona anlatımı.*

Bu belge konuya hiç yabancı olmayan biri için yazıldı. Önce kavramları gündelik benzetmelerle
anlatıyor, sonra sistemi, sonra matematiğini, en sonda da sonuçları ve sınırları. Matematik
bölümlerinde her sembolün ne anlama geldiği yazılıdır; formülleri atlayıp yalnız açıklamaları okumak
da bütün resmi anlamaya yeter. Paneli kullanmak için §11–§12'ye, oda krokisi ve deneyler için §13'e, teknik ayrıntının tamamı
için §15'teki belgelere bakın.

---

## 1. Tek cümlede proje

Bir Raspberry Pi'nin üzerindeki **bir Wi-Fi kartı radyo sinyali yayar**, **aynı cihazdaki ikinci bir
Wi-Fi kartı** bu sinyalin odadan geri dönen kısmını dinler. Odada biri hareket edince sinyal çok
küçük miktarda değişir; sistem bu değişimi matematikle ayıklayıp **alarm** üretir. Kamera yok,
giyilebilir cihaz yok, karşıda ikinci bir cihaz yok.

## 2. Neden böyle bir şey istiyoruz?

- **Mahremiyet:** Kamera görüntü kaydeder; Wi-Fi sinyali yüz ya da görüntü taşımaz.
- **Karanlık ve görüş hattı:** Kamera karanlıkta ve bir şeyin arkasında göremez; radyo dalgası ışık
  gerektirmez, ince engellerden geçebilir.
- **Asıl senaryo:** Bir kamyonetin kasasına bir cihaz koymak ve kasaya izinsiz biri girerse ya da
  bir şey hareket ederse haber almak. Ama sistem odada, depoda, kapıda da çalışacak şekilde
  tasarlandı.
- **Tek cihaz:** Projenin önceki sürümü iki cihazla çalışıyordu (biri yayın yapan "Bravo", öbürü
  dinleyen "Alpha"). İki cihazı ayrı yerlere kurmak, beslemek ve eşleştirmek zahmetli. Bu sürümde
  her şey tek cihazda: **Alpha** hem yayın yapıyor hem dinliyor.

## 3. Temel kavramlar (sade anlatım)

### 3.1 Radyo dalgası, frekans, dalga boyu
Wi-Fi sinyali, havada ışık hızıyla yayılan bir **radyo dalgasıdır**. Bir dalganın saniyede kaç kez
salındığına **frekans** denir. Bizim kullandığımız Wi-Fi kanalı yaklaşık **5,24 GHz**, yani saniyede
5,24 milyar salınım. İki dalga tepesi arasındaki mesafe olan **dalga boyu** bu frekansta yaklaşık
**5,7 cm**'dir. Bunun önemi şu: insan birkaç santimetre kıpırdasa bile dalganın "tepeleri" ile
"çukurları" o kişiye göre yer değiştirir. Wi-Fi bu yüzden küçük hareketlere duyarlıdır.

### 3.2 Çok yollu yayılım: yankı benzetmesi
Bir odada bağırdığınızı düşünün. Karşıdaki kişi sesinizi hem doğrudan, hem de duvardan, tavandan,
dolaptan yansıyan **yankılar** olarak duyar. Radyo dalgası da aynıdır: alıcıya pek çok farklı yoldan
gelir. Buna **çok yollu yayılım** (multipath) denir. Biri odada hareket edince bazı yankıların yolu
uzar ya da kısalır, ve alıcının duyduğu toplam "ses" değişir. **Algılamanın özü bu değişimi
yakalamaktır.**

### 3.3 Alt taşıyıcılar (OFDM): piyano tuşları benzetmesi
Wi-Fi, bilgiyi tek bir frekansta değil, yan yana dizilmiş çok sayıda küçük frekansta aynı anda
gönderir. Buna **OFDM**, bu küçük frekansların her birine **alt taşıyıcı** (subcarrier) denir. Bir
piyanoda aynı anda birçok tuşa basmak gibi düşünebilirsiniz. Bizim kanalımızda **48** alt taşıyıcı
kullanılıyor, aralarında 312,5 kHz var.

### 3.4 CSI: her tuşun nasıl duyulduğu
**CSI** (Channel State Information, kanal durum bilgisi), alıcının her alt taşıyıcı için "bu tuş bana
ne kadar yüksek ve hangi gecikmeyle ulaştı" bilgisidir. Her Wi-Fi çerçevesi için 48 sayılık bir liste
elde edilir. Saniyede yaklaşık **50 çerçeve** geldiği için, saniyede 50 kez yenilenen 48 sütunlu
bir tablo oluşur. Odada bir şey değişince bu tablonun değerleri de değişir.

### 3.5 Genlik, faz ve karmaşık sayı
Her CSI değeri iki bilgi taşır:
- **Genlik:** sinyalin ne kadar güçlü geldiği ("tuşun sesi ne kadar yüksek").
- **Faz:** dalganın hangi noktasında geldiği ("tuşa ne zaman basılmış gibi duyuldu").

Bu ikisini birlikte tutmanın en kolay yolu **karmaşık sayıdır**. Karmaşık sayıyı bir düzlemde bir
**ok** olarak düşünebilirsiniz: okun uzunluğu genliktir, yönü fazdır. Birden fazla yoldan gelen
sinyaller, bu okların uç uca eklenmesiyle toplanır.

### 3.6 Desibel (dB)
Radyo dünyasında güçler çok geniş bir aralıkta değiştiği için **logaritmik** bir ölçek kullanılır.
Bazı sabit noktalar:

| Fark | Güç oranı |
|---|---|
| 3 dB | 2 kat |
| 10 dB | 10 kat |
| 20 dB | 100 kat |
| 30 dB | 1 000 kat |
| 40 dB | 10 000 kat |

"−28 dB" demek, bir şeyin ötekinin yaklaşık **630'da biri** kadar güçte olması demektir.

### 3.7 RSSI
**RSSI**, kartın "bu çerçeveyi ne kadar güçlü aldım" diye verdiği tek bir sayıdır (ör. −19 dBm).
CSI'nın 48 değerine karşılık RSSI tek değerdir; çok kabadır.

### 3.8 Gürültü
Her ölçümde, ölçülen şeyle ilgisi olmayan küçük rastgele dalgalanmalar vardır: elektronik devrelerin
ısıl gürültüsü, sayısallaştırma (yuvarlama) hataları gibi. Algılama, **gerçek değişimi** bu
**rastgele dalgalanmadan** ayırt etme işidir.

## 4. Donanım: kim ne yapıyor?

| Parça | Ne işe yarıyor |
|---|---|
| **Raspberry Pi 5** ("Alpha") | Küçük, kart boyutunda bir bilgisayar. Bütün hesaplar burada yapılır. |
| **Intel AX210** Wi-Fi kartı | Hem internete/ağa bağlantı (SSH ve web paneli için), hem **yayın yapan** taraf. |
| **Broadcom** dahili Wi-Fi yongası | **Dinleyen** taraf. Normalde CSI vermez. |
| **Nexmon** | Broadcom yongasının yazılımına (firmware) eklenen bir yama. Yongayı CSI ölçebilen bir alıcıya çevirir. |
| **Web paneli** | Aynı ağdaki herhangi bir bilgisayardan açılan arayüz: canlı grafikler, kalibrasyon, alarmlar, ayarlar. |

**Sinyal yolları şeması:**

```text
                    ┌──────────────── Raspberry Pi 5 ("Alpha") ────────────────┐
                    │                                                           │
   erişim noktası   │   AX210 (yayın + ağ bağlantısı)      Broadcom + Nexmon    │
   (modem) ◄────────┼───(1) yönetim çerçevesi ─┐           (dinleyici, CSI)     │
       │            │                          │                ▲   ▲          │
       └──(ACK)─────┼──► AX210                 │ (2) SIZINTI    │   │          │
                    │                          └───────────────►┘   │          │
                    └──────────────────────────────┬────────────────┼──────────┘
                                                   │ (3) odaya      │ (4) geri dönen
                                                   ▼    giden       │     yansıma
                                               🧍 insan / kapı / eşya ┘
```

1. AX210 saniyede 50 küçük yönetim çerçevesi gönderir. Modem yalnızca "aldım" (ACK) der; ACK'te
   AX210'un adresi olmadığı için dinleyici onu dikkate almaz.
2. Çerçevenin büyük kısmı birkaç santimetre öteden doğrudan Broadcom'a ulaşır: **sızıntı**.
3. ve 4. Küçük bir kısmı odaya gider, insandan ve eşyalardan yansıyıp döner. Aradığımız bilgi
   burada.

**Monostatik ve bistatik.** Yayın yapan ile dinleyen ayrı yerlerdeyse düzene **bistatik** denir.
Önceki iki cihazlı sürüm böyleydi. İkisi aynı yerdeyse **monostatik** denir; radar böyle çalışır ve
bu sürüm de böyledir.

## 5. Asıl zorluk: kendi sesini duymak

Bir odada, ağzınızın 10 cm yanında bir mikrofon olduğunu düşünün. Siz konuşurken mikrofon **önce
ve en yüksek sesle sizi duyar**. Odanın öbür ucundaki duvardan dönen yankı ise binlerce kat daha
zayıftır.

Bizim cihazda tam olarak bu oluyor:
- AX210'un anteni ile Broadcom'un anteni birkaç santimetre uzakta. AX210'un yaydığı sinyalin büyük
  kısmı odaya hiç uğramadan doğrudan Broadcom'a gider. Buna **sızıntı** (leakage) diyoruz.
- Odadaki bir insandan dönen yansıma, sızıntıdan **yaklaşık 30–40 dB**, yani 1 000–10 000 kat
  daha zayıf.

Bu kadarı tek başına çözülebilir bir sorun olurdu: sızıntı **sabit** olduğu için onu bir kez öğrenip
çıkarabilirdik. Asıl zorluk ikinci kısım:

- Kart, aldığı sinyalin seviyesini her çerçevede kendiliğinden ayarlıyor. Buna **AGC** (otomatik
  kazanç kontrolü) denir; mikrofonun ses seviyesini kendi kendine açıp kısması gibi.
- İki kartın saatleri birbirine tam senkron değil. Bu yüzden her çerçevede ölçülen faz rastgele
  dönüyor (**CPE**) ve küçük bir zaman kayması oluşuyor (**STO**).
- AX210 her çerçeveyi biraz farklı güç ayarıyla yolluyor; ölçtüğümüz kadarıyla birkaç ayrık "güç
  durumu" arasında gidip geliyor.

Bunların hepsi, ölçülen sinyali **her çerçevede bilinmeyen bir sayıyla çarpıyor**. Dev sızıntı da bu
sayıyla çarpıldığı için, çarpandaki **%1'lik bir oynama bile**, aradığımız yansıma kadar büyük bir
hata üretiyor. Bunu **dev bir fon müziği** gibi düşünün: müziğin sesi her saniye rastgele biraz açılıp
kısılırken, arkadaki bir fısıltıyı duymaya çalışıyorsunuz.

## 6. Yolculuk: ne denendi, ne öğrenildi?

1. **Önceki yöntem işe yaramadı.** İki cihazlı sistemden kalan "CSI şeklindeki dalgalanma" ölçütü,
   tek cihazda kapı ve insan olaylarının hiçbirini yakalamadı (0/20). Dalgalanma, gürültüyle aynı
   büyüklükteydi.
2. **Modem tuzağı.** İlk tek cihaz denemesinde AX210'u, modemin cevaplarına karşılık kısa "alındı"
   çerçeveleri (BlockAck) göndermeye zorladık. 17/17 gibi çok iyi bir sonuç çıktı. Ama ayrıntılı
   inceleme, bunun **yansımayı değil, AX210 ile modem arasındaki bağlantının bozulmasını**
   ölçtüğünü gösterdi. Kişi aradan geçince AX210 güç ayarını değiştiriyordu. Modem olmayan bir
   kamyonette bu çalışmazdı. Bu, literatürde kolayca gözden kaçabilecek önemli bir tuzak.
3. **Gerçek tek kutu kaynağı.** AX210'a, bağlı olduğu ağın erişim noktasına adresli küçük
   **yönetim çerçeveleri** gönderttik (saniyede 50). Bu çerçeveler her zaman aynı, en düşük hızla
   gidiyor ve modemin cevabına bağlı değil. AX210 ağa bağlı kalıyor, uzaktan erişim bozulmuyor.
4. **Denenip elenenler:** AX210'da ikinci bir erişim noktası açmak (yasal kanal kısıtı), izleme
   modunda çerçeve enjekte etmek ve herkese yayın yapan çerçeveler (sürücü bunları havaya
   göndermiyor), anteni sabitlemek (sürücü desteklemiyor), gücü sınırlamak (durumları gidermedi).
5. **Matematiksel çözüm:** sızıntıyı ve kart kaynaklı bozucuyu ayıklayan yöntem (§7). Önceden
   kuralları belirlenmiş bağımsız bir testte sınandı (§8).

## 7. Çözümün mantığı ve matematiği

### 7.0 Bütün hat tek bakışta

```text
 Wi-Fi çerçevesi (50/sn)
   │
   ▼
 [Süzgeç] yanlış tür / yeniden gönderilmiş çerçeveleri at
   │
   ▼
 [CSI]  48 alt taşıyıcı × (genlik, faz)
   │
   ▼
 [Adım 1] zaman kaymasını (STO) düzelt
   │
   ▼
 [Adım 2] sızıntıyı sil:  r = h/ĝ − D        ← D: kalibrasyonda öğrenildi, durum başına izlenir
   │
   ▼
 [Adım 3] beyazlat:  z = C^(−1/2)(x − μ)      ← C: boş odanın "gürültü parmak izi"
   │
   ▼
 [Adım 4] T² = son 3 sn  vs  önceki 17 sn     ← her 0.5 sn
   │
   ▼
 [Adım 5] eşik + "4 kararın 3'ü" kuralı
   │
   ▼
 ALARM / NORMAL  →  panel, alarm geçmişi, olay dosyası
```

Aynı anda başka dedektörler de çalışır: statik değişim, durum profilleri, mikro-hareket ve
sinyal/sabotaj. Bunlar §11.4'te anlatılıyor.

### 7.1 Ölçtüğümüz şeyin modeli

Alt taşıyıcı `k` ve çerçeve `t` için ölçülen CSI değeri:

```text
H_k(t)  =  g(t) · e^{−j·k·θ(t)} · D_k   +   (insandan/nesneden gelen yansımalar)   +   gürültü
```

| Sembol | Anlamı | Benzetme |
|---|---|---|
| `D_k` | Sızıntı + duvar/eşya yansımaları; zamanla **değişmeyen** kısım | Odanın sabit "fon müziği" |
| `g(t)` | Her çerçevede değişen bilinmeyen karmaşık çarpan (AGC, faz dönmesi, güç durumu) | Müziğin sesini rastgele açıp kısan el |
| `θ(t)` | Saatler arası küçük zaman kayması | Kaydın birkaç milisaniye kayması |
| yansımalar | İnsanın hareketinden gelen, aradığımız küçük kısım | Fısıltı |

Amaç: `g`, `θ` ve `D` ne olursa olsun, **yansımalardaki değişimi** ortaya çıkarmak.

### 7.2 Adım 1: zaman kaymasını düzeltmek

Zaman kayması, alt taşıyıcılar boyunca düzenli artan bir faz dönmesi olarak görünür; tuşlar
soldan sağa doğru gittikçe daha "geç" çalıyormuş gibi. Komşu tuşların farkına bakarak bu eğim
bulunur ve geri çevrilir:

```text
θ̂ = açı( Σ_k  H_{k+1} · H_k* )        H_k  ←  H_k · e^{+j·k·θ̂}
```

(`*` karmaşık eşleniği, yani okun yönünü ters çevirmeyi gösterir.)

### 7.3 Adım 2: sızıntıyı tam olarak silmek (en küçük kareler izdüşümü)

Önce boş odada bir süre ölçüm yaparak sızıntının "şeklini" (`D`) öğreniriz. Buna **kalibrasyon**
denir. Sonra her yeni çerçevede şu soruyu sorarız: "Bu çerçeve, `D`'nin hangi katı olsaydı ona en
çok benzerdi?"

```text
ĝ = (Dᴴ · h) / (Dᴴ · D)          ← "en uygun çarpan"
r = h / ĝ − D                    ← "artık": çerçevenin D ile açıklanamayan kısmı
```

**Gölge benzetmesi:** `h`'yi bir ok, `D`'yi bir duvar yönü olarak düşünün. `ĝ·D`, okun o duvar
üzerindeki gölgesidir. Artık `r` ise gölgede kalmayan, duvara **dik** kısımdır.

Bu adımın iki matematiksel özelliği var:
1. **Diklik:** artık, sızıntıya tam diktir. Sızıntının hiçbir parçası artığa sızmaz.
2. **Değişmezlik:** ölçümün tamamı herhangi bir sayıyla çarpılsa (kart sesi açsa, faz dönse, güç
   durumu değişse) `ĝ` de aynı sayıyla çarpılır ve artık **hiç değişmez**.

Yani "sesi rastgele açıp kısan el" bu adımda tamamen etkisiz kalır. Ölçümde artığın gücü sızıntıya
göre −28 dB çıktı. Bu, kartın kendi gürültü seviyesi; sızıntıdan geriye bir şey kalmıyor.

**Küçük sayısal örnek** (2 alt taşıyıcı, gerçek sayılarla):

```text
D = (10, 5)                       ← öğrenilmiş sızıntı şekli
h = (20.2, 9.8)                   ← kart sesi 2 katına açmış + küçük bir yansıma
ĝ = (10·20.2 + 5·9.8) / (10² + 5²) = 251 / 125 = 2.008
r = h/ĝ − D = (10.06 − 10, 4.88 − 5) = (+0.06, −0.12)
```

Kazancın 2 katına çıkması tamamen yok oldu. Kalan küçük `(+0.06, −0.12)` yansımanın izidir, ve
`D` ile skaler çarpımı sıfırdır: `10·0.06 + 5·(−0.12) = 0`.

### 7.4 Bir bedel: yakın yansımanın çoğu "gölgeye" düşer

Bir yansımanın yolu sızıntıdan ne kadar uzunsa, alt taşıyıcılar boyunca o kadar çok "döner" ve
sızıntıdan o kadar farklı görünür. Kısa yollu, yani yakın bir yansıma ise sızıntıya çok benzer;
büyük kısmı gölgeye (`ĝ`'ye) gider. Artıkta kalan pay:

```text
η(τ) = 1 − |Dᴴ s_τ|² / (‖D‖² · ‖s_τ‖²),      s_τ = D ⊙ e^{−j·2π·f_k·τ}
```

| Yansımanın fazladan yolu (≈ uzaklık) | Artıkta kalan pay |
|---|---|
| 0,75 m | %2 |
| 1,5 m | %9 |
| 3 m | %32 |
| 4,5 m | %60 |
| 7,5 m | %97 |

Bu, deneyde gördüğümüz bir şeyi açıklıyor. Yakın hareket artıkta zayıf kalıyor ama "ortak güçte"
(`|ĝ|`'de) çok belirgin. Uzak hareket ise artıkta görünüyor. Yakın hareket yine de artıkta
yakalanıyor (10/10), çünkü yakın olduğu için güçlü.

### 7.5 Adım 3: beyazlatma, yani gürültünün parmak izini öğrenmek

Artık 48 karmaşık sayıdan oluşur; gerçek ve sanal kısımlarıyla **96 sayı**. Boş odada bu 96 sayının
hepsi aynı ölçüde oynamaz. Bazı yönlerde (ya da sayı kombinasyonlarında) gürültü büyüktür, bazılarında
çok küçüktür. Bu oynama örüntüsüne **kovaryans** denir: "hangi sayılar ne kadar ve birlikte nasıl
oynuyor" tablosu.

**Beyazlatma**, artığı bu tabloya göre yeniden ölçeklemektir. Gürültünün büyük olduğu yönleri
küçültür, küçük olduğu yönleri büyütür; sonunda her yönde gürültü aynı boyda (1) olur:

```text
z = C^{−1/2} · (x − μ)        x = artığın 96 sayısı, μ = boş odadaki ortalaması, C = kovaryans
```

**Neden bu kadar önemli?** Bir insanın hareketi artığı genellikle boş odada **az oynayan**
yönlerde değiştirir. Beyazlatma yapılmazsa bu küçük değişim, gürültünün büyük olduğu yönlerin
arasında kaybolur. Deneyde beyazlatmayı çıkarınca bağımsız olayların **21/27'sinden 0/27'sine**
düştük. **Yöntemin en kritik adımı budur.**

**Ledoit–Wolf düzeltmesi:** 96 sayılık bir kovaryansı, sınırlı sayıda ölçümden tahmin etmek
risklidir; az görülen yönlerin gürültüsü olduğundan küçük çıkar ve beyazlatma onları aşırı büyütür.
Ledoit–Wolf yöntemi, tahmini matematiksel olarak en uygun ölçüde "ortalamaya doğru çekerek" bunu
önler.

### 7.6 Adım 4: T² testi, yani "son 3 saniye öncekinden farklı mı?"

Her yarım saniyede bir şu karşılaştırma yapılır:
- **Pencere:** son 3 saniyenin beyazlatılmış artıklarının ortalaması.
- **Taban:** ondan önceki 17 saniyenin (20 sn'lik geçmiş) ortalaması.

```text
T²(t) = (n_W · n_B / (n_W + n_B)) · ‖ ortalama_pencere − ortalama_taban ‖² / 96
```

(`n_W`, `n_B`: penceredeki ve tabandaki çerçeve sayısı.)

**Neden böyle?** Gürültü her yönde 1 boyunda olduğu için, ortada gerçek bir değişim yoksa bu sayı
istatistik teorisine göre **ortalama 1** civarında çıkar (ki-kare dağılımı). Ölçtük: boş odada
medyan 0,90–0,95. Odada biri hareket edince ise 3–20 ve üstüne çıkar.

Bu bir **Hotelling T²** testidir: iki grup çok boyutlu ölçümün aynı ortalamaya sahip olup olmadığını
soran klasik istatistik testi.

### 7.7 Adım 5: karar, yani alarm ne zaman çalar?

- **Eşik:** kalibrasyon sırasında (boş oda, 3 dk) T²'nin aldığı değerlerden otomatik belirlenir;
  pratikte 2–3 civarı.
- **3'te 4 kuralı:** son 4 kararın en az 3'ünde T² eşiği geçerse alarm başlar; 4 kez üst üste
  sakin kalırsa biter. Tek bir rastgele sıçrama alarm üretmez.
- **Kalibrasyon süresi neden 3 dakika?** 1 dakikalık kalibrasyon, boş odadaki nadir büyük
  dalgalanmaları göremedi ve eşiği düşük tuttu. 3 dakikayla boş odadaki yanlış alarm, 20 dakikada
  5'ten 15 dakikada 1'e indi.

### 7.8 Kartın "ruh hali": güç durumları

AX210 çerçeveleri birkaç ayrık güç durumunda gönderiyor (ölçülen RSSI −19 / −21 / −23 dBm).
Durumların sızıntı şekli aynı, ama gürültü karakterleri farklı. Bir kez durum kalıcı olarak
değişti; eski durumun gürültü parmak iziyle bakılınca boş odada T² 65'e fırladı. Bu yüzden:
- Her durumun kendi `D`'si ve kendi beyazlatması öğreniliyor.
- T² yalnız o anki baskın durumun çerçeveleriyle hesaplanıyor.
- Durum değişince 20 saniye bekleniyor.

## 8. Sonuçlar ve nasıl okunacakları

### 8.1 Terimler
- **Yakalama (recall):** gerçekleşen olayların kaçında alarm çaldı. "21/27" = 27 olayın 21'inde.
- **Yanlış alarm:** kimse yokken çalan alarm. Saat başına sayı ve alarmda geçen süre olarak verilir.
- **Bağımsız test:** yöntem bir kayıtta geliştirilir, ama **başka** kayıtlarda, hiçbir ayara
  dokunmadan ve kurallar önceden yazılarak sınanır. Böylece "sonucu kendi verisine uydurma" engellenir.
- **Güven aralığı:** az sayıda denemeyle ölçülen oranın gerçekte ne aralıkta olabileceği.
  21/27 için %95 güven aralığı kabaca %59–89.

### 8.2 Canlı bağımsız test (aynı oda, kurallar önceden sabit)

| Durum | Sonuç |
|---|---|
| Boş oda, 15,5 dk | **1 alarm** (alarmda geçen süre %0,9) |
| Yakın hareket (sandalye, yatakta el sallama, hareketsiz durup derin nefes) | **10/10** |
| Orta mesafe yürüme (2–4 m) | **5/5** |
| Kapı açma/kapama, odaya giriş (4–5 m) | 8/12 |
| Kapı kapalıyken oda dışında hareket (yakalanmaması istenir) | 1/5 |

### 8.3 Diğer yöntemlerle karşılaştırma (aynı yanlış alarm seviyesinde)

| Yöntem | Bağımsız oda içi olaylar |
|---|---|
| **Bu projenin yöntemi** | **21/27** |
| Klasik CSI genlik dalgalanması | 15/27 |
| Arka plan çıkarma (normalize edilmiş, beyazlatmasız) | 2/27 |
| Projenin önceki yöntemi | 0/27 |

## 9. Sınırlar ve dürüst notlar

- **Menzil:** cihaza 2–4 m'ye kadar çok iyi; 4–5 m'de yaklaşık üçte iki. Daha uzağı zayıf.
- **Oda dışı:** kapı açıksa ya da duvar inceyse kapının hemen dışındaki hareket de görülebilir.
  Metal bir kamyonet kasası dışarıyı büyük ölçüde keser.
- **Hareketsiz insan:** sistem bir **değişim** detektörü. Tamamen hareketsiz ve sığ nefes alan biri
  20 saniye sonra "yeni normal" olabilir. Derin nefes yakalandı; sakin nefes henüz ölçülmedi.
- **Veri miktarı:** tek oda, tek gün, tek kişi. Sonuçlar umut verici ama genellenmesi için farklı
  ortamlarda ve kamyonette tekrarlanmalı.
- **Donanım tuhaflıkları:** Broadcom yongası nadiren veri göndermeyi bırakıyor. Sistem bunu 20
  saniyede fark edip kendini yeniden başlatıyor.
- **Mesafeyi ölçemiyoruz:** 20 MHz'lik bantla yansımaların uzaklığını doğrudan ayırmak mümkün değil.
  Yalnız "bir şey değişti" denebiliyor.

## 10. Sistemin parçaları

| Parça | Dosya | Görevi |
|---|---|---|
| Algılama motoru | `device-files/csi-alpha-webui/webui/csi_sensing.py` | Çerçeveleri işler, dedektörleri çalıştırır, alarm kararını verir |
| Sızıntı iptali + T² | `.../webui/csi_residual.py` | §7.3–7.7'deki matematik |
| Yönetim çerçevesi göndericisi | `.../webui/csi_nl80211.py` | AX210'a saniyede 50 çerçeve gönderttirir |
| Web sunucusu ve yakalama | `.../webui/csi_web.py` | Nexmon akışını okur, paneli sunar, takılmaları toparlar |
| Panel | `.../webui/index.html`, `app.js`, `charts.js` | Canlı grafikler, kalibrasyon, olay işaretleme, analiz |
| Ağ yönetimi | `.../webui/csi_network.py` | Başka Wi-Fi ağına geçiş, kurulum hotspot'u |
| Testler ve değerlendirme | `tools/csi_sensing/` | Birim testleri, kayıt tekrar oynatma, karşılaştırma tabloları |

**Kullanım akışı:** Paneli aç → Kurulum'da **Kalibre et** (oda boşken, 3 dk) → sistem kurulur →
olayları canlı izle → kayıtları Analiz sekmesinde tekrar oynat.

## 11. Paneli kullanmak

Panel, cihazın içinde çalışan bir web sayfasıdır. Cihazla aynı ağdaki herhangi bir bilgisayardan ya
da telefondan açılır:

- **Normal kullanım:** `http://csi-alpha.local:8080` (çalışmazsa cihazın IP adresi, ör. `http://192.168.1.99:8080`)
- **Cihaz hiçbir ağa bağlanamazsa:** cihaz kendi Wi-Fi'ını açar (`csi-alpha-kurulum`, parola
  `csialpha2026`). Ona bağlanıp `http://10.42.0.1:8080` adresine gidin.

Bütün ayarlar, kalibrasyon ve kayıtlar **cihazda** saklanır. Hangi bilgisayardan açarsanız açın
aynı sistemi görürsünüz.

### 11.1 Üst çubuk

| Öğe | Anlamı |
|---|---|
| **Durum etiketi** (ör. "Bekliyor", "Çalışıyor") | Yakalamanın açık olup olmadığı. |
| **"… kare/sn"** | Saniyede kaç CSI ölçümü geldiği. Normali 40–55. |
| **Mod seçimi** | **Tek kutu** (bu proje) ya da **Alpha–Bravo (eski)** (iki cihazlı eski sistem). |
| **Başlat / Durdur** | Ölçümü başlatır ya da durdurur. Başlat'ta Broadcom alıcısı yeniden yüklenir ve yeni bir kayıt dosyası açılır. |
| **Kurulu** | Alarmı kurar ya da kapatır. Kapalıyken sistem ölçer ve hesaplar, ama alarm kaydı üretmez (odada çalışırken kullanışlı). Kurunca statik referans o anki ortama tazelenir. |
| **◐** | Açık/koyu tema. |

### 11.2 Genel durum etiketleri

| Etiket | Anlamı | Ne yapmalı |
|---|---|---|
| **BEKLİYOR** | Ölçüm başlatılmamış. | Başlat'a basın. |
| **KALİBRE EDİLMEDİ** | Sistem odanın "normal" halini bilmiyor. Alarm veremez. | Kurulum → Kalibre et (oda boşken). |
| **KALİBRASYON** | Kalibrasyon sürüyor. | Odaya girmeyin, bitmesini bekleyin. |
| **PROFİL KAYDI** | Bir durum profili kaydediliyor. | Ortamı o durumda sabit tutun. |
| **NORMAL** | İzleniyor, alarm yok. | — |
| **ALARM** | En az bir dedektör alarmda. | Hangisi olduğu kartlarda ve "Son alarmlar"da yazar. |
| **SİNYAL YOK** | CSI ölçümü gelmiyor. | Birkaç saniye bekleyin; sistem 20 sn içinde kendini yeniden başlatır. Sürerse Kurulum → Radyo kontrolü. |

### 11.3 Canlı sekmesi: üstteki dört kutu

| Kutu | Büyük sayı | Alt satır |
|---|---|---|
| **Geçerli kare** | Saniyede işlenen geçerli CSI ölçümü. | "ham": süzgeçten önceki sayı. "nfft 64": 20 MHz'lik ölçüm (64 noktalı). |
| **RSSI** | Anlık sinyal gücü (dBm). | Kalibrasyondaki değer. Büyük fark, cihazın ya da antenin yerinin değiştiğini gösterir. |
| **PHY modu** | Son birkaç saniyede görülen farklı "şekil modu" sayısı. Normali 1. | "baskın %": en sık modun payı. "banka": öğrenilmiş mod sayısı. |
| **Kanal · kaynak** | Dinlenen kanal (ör. `48/20`: kanal 48, 20 MHz). | TX yöntemi (`ax210_action`), arayüz (`mgmt0`), saniyedeki çerçeve sayısı (pps). |

**PHY modu ne demek?** Wi-Fi çerçeveleri farklı biçimlerde gönderilebilir (hız, akış sayısı vb.).
Her biçim CSI'da biraz farklı bir "şekil" bırakır. Sistem bu şekilleri gruplayıp her grubu kendi
içinde karşılaştırır, böylece biçim değişimi hareket sanılmaz.

### 11.4 Canlı sekmesi: beş dedektör kartı

Her kartın sağ üstünde bir **rozet** (kapalı / bekliyor / normal / ALARM), ortada bir **skor** ve
altında ayrıntı satırı vardır. **Skor = ölçülen değer ÷ eşik.** %100'ü geçmek, eşiği aşmak demektir.

**1. Hareket.** Ana dedektör. Ayrıntı satırında:
- `değer … / eşik …`: CSI şekil dalgalanması (eski ölçüt). Tek kutuda neredeyse hiç tetiklenmez.
- `yenilik %…`: bilinen hiçbir şekil moduna benzemeyen çerçevelerin oranı.
- **`T² … / …`**: sızıntı iptalli değişim istatistiği ve eşiği (§7.6). **Asıl karar bu.** Boş odada ≈1.
- `kazanç %…`: verici güç durumu geçiş payı (yalnız `ax210_ba` yönteminde).

Bu kanallardan herhangi biri eşiği geçerse skor yükselir. Alarm "4 kararın 3'ü" kuralıyla verilir.

**2. Statik değişim · nesne.** Ortamda **kalıcı** bir değişiklik var mı (kapı açık kaldı, kutu
bırakıldı, eşya yer değiştirdi)?
- `z …`: şimdiki ortamın, öğrenilmiş "normal" ortamdan uzaklığı. Gürültü biriminde ölçülür;
  "kaç standart sapma uzakta" gibi düşünün.
- `sessiz … sn`: statik karar ancak hareket bitince verilir. Bu, ne kadar süredir sakin olduğunu
  gösterir.
- `uyarlanan … sn`: referansın yavaş sıcaklık ve kayma etkilerini izlemek için ne kadar süredir
  güncellendiği.

**3. Durum profili.** Önceden kaydedilmiş durumlardan (ör. `kapali`, `acik`) hangisine benzediği.
Hiçbirine benzemiyorsa **bilinmeyen**. Alarm üretecek etiketler Ayarlar'dan seçilir.

**4. Mikro-hareket (deneysel).** Hareketsiz bir insanın nefesi gibi 0,15–0,6 Hz'lik (yaklaşık
4–6 saniyede bir) düzenli küçük değişimleri arar. `tepe/medyan`: o banttaki en güçlü frekansın
ortalamaya oranı. Varsayılan olarak kapalı.

**5. Sinyal · sabotaj.** Ölçüm akışı sağlıklı mı? `asgari …/sn`: bunun altına düşerse sinyal kaybı
sayılır. `RSSI Δ`: sinyal gücündeki düşüş. Anten kapatılırsa ya da cihaz örtülürse büyür.

### 11.5 Canlı sekmesi: grafikler

**Zaman serisi.** Son 1 / 5 / 15 dakikayı gösterir (❚❚ duraklatır). Fareyle üzerine gelince
değerler görünür. **Kırmızı bant**: alarm süresi. **Dikey çizgi**: sizin işaretlediğiniz olay.
**Kesikli çizgi**: o izin eşiği. İzler:

| İz | Anlamı |
|---|---|
| Skorlar | Hareket, statik ve mikro skorlarının birlikte gösterimi. 1 çizgisi = eşik. |
| Hareket metriği (mod içi std) | Eski şekil dalgalanması ölçütü. |
| **Sızıntı iptalli T² (değişim)** | Asıl hareket istatistiği ve eşiği. |
| Kazanç durumu payı | Yalnız `ax210_ba`'da anlamlı. |
| Statik z-mesafe | Kalıcı değişim uzaklığı ve eşiği. |
| Kare hızı (/sn) | Ölçüm akışının sağlığı. |
| RSSI (dBm) | Sinyal gücü. |

**Spektrogram (PCA-1, son 60 sn).** Önce 48 alt taşıyıcının **en çok birlikte değiştiği yön**
bulunur (PCA-1, "birinci temel bileşen"; bütün tuşların ortak hareketini tek bir sayıya indirmek
gibi). Sonra bu sayının hangi hızlarda (frekanslarda) dalgalandığı renkle gösterilir: **STFT**,
kısa zamanlı Fourier dönüşümü. Yatay eksen zaman, dikey eksen frekans (Hz), parlaklık enerji.
Hızlı hareket yukarılarda, yavaş değişim en altta görünür.

**Alt taşıyıcı sapma haritası (son 60 sn).** Her satır bir ton grubu, her sütun bir an. Renk, o
tonun genliğinin son bir dakikalık ortalamasından sapması (mavi = düşük, kırmızı = yüksek).
**Dikey çizgiler** kısa hareketleri, **yatay bantlar** kalıcı değişimleri gösterir.

### 11.6 Canlı sekmesi: olay işaretleme ve son alarmlar

**Olay işaretle (yer gerçeği).** Deney yaparken "gerçekte ne oldu" bilgisini sisteme vermek için.
1. **Olay adı** kutusuna bir ad yazın ya da seçin (ör. `kapi_acildi`, `bos_oda`).
2. Olay başlarken **Başladı**'ya, bitince **Bitti**'ye basın. Tek anlık olaylar için **Anlık**.
3. İşaretler kayıt dosyasına yazılır. Analiz sekmesi bunlardan yakalama oranını ve gecikmeyi hesaplar.

**İyi uygulama:** her denemeden önce durumun "NORMAL"e dönmesini bekleyin. Yürüyerek yer
değiştiriyorsanız, yerinize vardıktan sonra Başladı'ya basın.

**Son alarmlar.** En yeni alarmlar ve sebebi (hareket, statik değişim, sinyal kaybı…). "Tümü",
Kayıtlar sekmesine götürür.

### 11.7 Analiz sekmesi: kayıtları tekrar oynatmak

Her Başlat–Durdur arası bir kayıttır (`.pcap`). Analiz, kaydı canlı sistemle **aynı motorla** ve
**şu anki ayarlarla** baştan çalıştırır. Bir ayarı değiştirince etkisini eski kayıtlarda görmek
için kullanılır.

1. Soldaki listeden bir **kayıt** seçin.
2. **Kalibrasyon kaynağı:**
   - *Cihazdaki mevcut kalibrasyon:* şu an kayıtlı olanı kullanır.
   - *Bu kaydın bir aralığı:* kaydın içindeki boş bir bölümü (**Başlangıç / Bitiş**, saniye)
     kalibrasyon olarak kullanır.
3. **En fazla (sn):** çok uzun kayıtların yalnız ilk kısmını işler.
4. **Analiz et.**

**Sonuç kutuları:**

| Kutu | Anlamı |
|---|---|
| Süre | Kaydın uzunluğu. |
| Geçerli kare | Ortalama ölçüm hızı. |
| Hareket aktif / Statik aktif | Sürenin yüzde kaçı alarmda geçti. |
| Alarm | Toplam alarm sayısı ve saat başına. |
| **Recall** | İşaretli olayların kaçında alarm çaldı (yakalama oranı). |
| **Gecikme p50 / p90** | Olay başlangıcından alarma kadar geçen süre. p50 = olayların yarısında bundan kısa; p90 = %90'ında bundan kısa. |
| **Yanlış alarm** | İşaretli olayların dışında çalan alarm sayısı ve saat başına. |

Grafiklerde **sürükleyerek yakınlaştırın**, **çift tıklayarak** ya da **Sıfırla** ile geri dönün.
Bütün grafikler aynı zaman eksenini paylaşır. **CSV / JSON** sonucu dosya olarak indirir. "PCA-1
sinyali" spektrogramın üretildiği ham sinyaldir. "Alarmlar ve olaylar" listesi, alarmları ve sizin
işaretlerinizi zaman sırasıyla verir.

### 11.8 Kurulum sekmesi: dört adım

1. **Radyo kontrolü.** AX210'un hangi ağa, hangi kanalda, hangi genişlikte bağlı olduğunu ve
   uyarıları gösterir. Örnek: modem 802.11ax (HE) ise AX210'un veri çerçeveleri okunamaz, bu yüzden
   yönetim çerçevesi yöntemi kullanılır. **Yenile** ile tekrar okunur.
2. **Kalibrasyon.** Oda **boş ve sakinken** sistemin "normal"i öğrenmesi.
   - **Süre (sn):** varsayılan 180. Daha uzun süre daha sağlam eşik verir.
   - **Kalibre et / İptal.**
   - **Referansı tazele:** yalnız statik referansı şimdiki ortama eşitler. Kalibrasyonu ve hareket
     eşiklerini değiştirmez. Örneğin bir eşya kalıcı olarak yer değiştirdiyse kullanılır.
   - **Özet:** tarih, süre ve kare sayısı, PHY modu sayısı, **sızıntı iptali** (artık seviyesi dB ve
     verici durumu), eşikler, kanal ve kaynak. Uyarılar ve "Uyumsuzluk" satırları da burada
     görünür. Örneğin TX yöntemi ya da kanal değiştiyse yeniden kalibrasyon istenir.
3. **Durum profilleri (isteğe bağlı).** Ortamın farklı "normal" hallerini öğretmek için (ör.
   `kapali`, `acik`, `dolu`, `bos`). **Etiket** ve **Süre** girip **Kaydet**. Her profil ayrı
   kaydedilir. Canlıda en yakın profil seçilir.
4. **Kur ve izle.** Üst çubuktaki **Kurulu**'yu açıp Canlı sekmesine geçin.

### 11.9 Ağ sekmesi

| Bölüm | Ne işe yarar |
|---|---|
| **Bağlantı** | Şu anki ağ (SSID), IP adresi ve panelin bu ağdaki adresi. |
| **Kayıtlı ağlar** | Cihazın bildiği ağlar. Açılışta **öncelik** sırasıyla denenir; hiçbiri yoksa Ethernet, o da yoksa kurulum hotspot'u açılır. Öncelik değiştirilebilir, ağ silinebilir. |
| **Yeni ağa bağlan** | **Tara** çevredeki ağları listeler. **SSID** (ağ adı), **Parola**, **Gizli ağ** (adını yayınlamayan ağ) ve **Öncelik** girip **Bağlan**. Panel bu ağdan kopar; cihaz yeni ağda `csi-alpha.local:8080`'de olur. Bağlanamazsa 1–2 dk içinde eski ağa, o da olmazsa hotspot'a döner. |
| **Kurulum hotspot'u ve erişim** | **Hotspot SSID / parolası.** **Bağlantı yoksa otomatik aç** ve **Bekleme (sn)**: ağ bulunamazsa kaç saniye sonra hotspot açılacağı. **Panel parolası**: boş değilse paneldeki değişiklikler parola ister. **Hotspot'u aç / kapat**: elle açma (bağlantı kopar). |
| **Ağ değişikliği günlüğü** | Ağ geçişlerinin kaydı. Sorun gidermek için. |

### 11.10 Ayarlar sekmesi

Üstte **senaryo** seçimi ve bir arama kutusu vardır. **⟳** işaretli ayarlar ancak **Başlat**'ta
etkili olur; diğerleri anında uygulanır. Her alanın altında varsayılan değer ve kısa açıklama
yazar. **Kaydet** cihazda saklar, **Varsayılanlar** seçili senaryonun varsayılanlarına döner.
Bütün ayarların sade açıklaması §12'dedir.

### 11.11 Kayıtlar sekmesi

- **Alarm geçmişi:** bütün alarmlar (zaman, tür, skor). **Tümünü sil** geri alınamaz.
- **Dosyalar:** her kayıt için üç dosya:
  - `.pcap`: ham ölçümler (Analiz sekmesi bunu kullanır).
  - `.sensing.ndjson`: her yarım saniyedeki kararlar ve değerler.
  - `.events.ndjson`: alarmlar, kalibrasyon olayları ve sizin işaretlediğiniz olaylar.
- **Eski deneyler:** iki cihazlı Alpha–Bravo sisteminin ayarları (Bravo MAC, kanal, mesafe, veri
  seti etiketi, CNN/LSTM modeli) ve "süreli branda yansıma ölçümü". Tek kutu kullanımında gerekmez.
- **Sistem günlüğü:** cihazın teknik mesajları (ör. "RX takıldı → yeniden başlatılıyor").

### 11.12 Tipik iş akışları

**İlk kurulum**
1. Paneli açın → **Başlat**.
2. Kurulum → Radyo kontrolü'nde uyarı yok mu, bakın.
3. Odayı boşaltın → **Kalibre et** (180 sn) → "Kalibrasyon tamam".
4. **Kurulu**'yu açın → Canlı sekmesinde izleyin.

**Deney yapmak** (tez ölçümü)
1. Boş odada kalibrasyon.
2. `bos_oda` ile 20–30 dk boş kayıt (yanlış alarm ölçümü).
3. Her olayı Başladı/Bitti ile işaretleyerek 5–10 tekrar.
4. Analiz sekmesinde Recall, Gecikme ve Yanlış alarm'a bakın.

**Hassasiyeti ayarlamak**
- Çok yanlış alarm varsa: Ayarlar → Hareket → **Eşik çarpanı** 1.25–1.5, ya da **T² alt eşik** 2.5.
- Uzak hareket kaçıyorsa: eşik çarpanı 0.8–0.9 (yanlış alarm artar).
- Her değişikliğin etkisini Analiz sekmesinde eski kayıtlarla kontrol edin.

**Sorun giderme**

| Belirti | Olası sebep | Çözüm |
|---|---|---|
| SİNYAL YOK | Broadcom alıcısı takıldı | 20 sn bekleyin (otomatik toparlanır); sürerse Durdur → Başlat |
| Kare hızı < 20 | Kanal kalabalık ya da AX210 bağlantısı zayıf | Radyo kontrolü; gerekirse başka ağ/kanal |
| Sürekli ALARM, oda boş | Kalibrasyon eski ya da cihaz/anten yer değiştirdi | Yeniden kalibrasyon |
| Statik alarm kapanmıyor | Ortam kalıcı değişti (kapı açık kaldı vb.) | Değişiklik normalse **Referansı tazele** |
| "Uyumsuzluk: backend/kanal" | Ayar ya da ağ değişti | Yeniden kalibrasyon |

## 12. Bütün ayarlar ve yöntemler

### 12.1 Senaryolar (hazır ayar paketleri)

| Senaryo | Ne için | Öne çıkan ayarlar |
|---|---|---|
| **room**: Oda / iç mekân | İnsan hareketi ve kalıcı değişim | Mobilya değişirse statik referans 15 dk sonra kendini yeniler; mikro-hareket kapalı |
| **vehicle_cargo**: Kamyonet kasası | Park halindeki kasada açılma, içeride hareket, saklanan kişi, sabotaj | Statik değişim kendiliğinden silinmez; mikro-hareket ve RSSI düşüşü açık; alarm bekleme 30 sn; `open, acik, branda_acik, kesik` durumları alarm |
| **door**: Kapı / geçiş | Kapı açık/kapalı ve geçiş | Kısa pencereler, hızlı tepki |
| **warehouse**: Depo / raf | Nesne bırakma/alma | Hareket daha az hassas (×1.5), statik daha hassas ve uzun pencereli |
| **custom**: Özel | Elle ayar | Ön ayar uygulanmaz |

### 12.2 TX (yayın) yöntemleri

| Yöntem | Nasıl çalışır | Durum |
|---|---|---|
| **ax210_action** | AX210, bağlı olduğu erişim noktasına saniyede 50 küçük yönetim çerçevesi gönderir; Broadcom bunları okur | **Önerilen.** Gerçek tek kutu, sabit hız, modem cevabına bağlı değil |
| ax210_ba | Pi modeme ping atar, AX210 her cevaba "BlockAck" çerçevesi yayar | Çalışır, ama ölçtüğü şey kısmen AX210–modem bağlantısı (§6) |
| ap_udp | AX210'un normal veri çerçeveleri | Yalnız modem 802.11ac/80 MHz ise okunabilir |
| inject | AX210 "izleme" modunda ham çerçeve gönderir | AX210 bir ağa bağlıyken sürücü göndermiyor; ancak bağlantısızken |

### 12.3 Ayarlar tek tek

Sütunlar: paneldeki ad (koddaki ad) · varsayılan · ne işe yarar · ne zaman değiştirilir.
**⟳** = Başlat'ta etkili.

**Genel**

| Ayar | Varsayılan | Ne işe yarar | Ne zaman değiştirilir |
|---|---|---|---|
| Senaryo (`scenario`) | room | Hazır ayar paketini uygular (§12.1) | Ortam değişince |
| Alarm kurulu (`armed`) | açık | Kapalıyken hesap sürer ama alarm kaydı üretilmez | Odada çalışırken kapatın |

**Radyo**

| Ayar | Varsayılan | Ne işe yarar | Ne zaman değiştirilir |
|---|---|---|---|
| TX yöntemi (`txBackend`) ⟳ | ax210_action | AX210'un nasıl yayın yapacağı (§12.2) | Genelde değiştirmeyin |
| Kanal (`channel`) ⟳ | auto | Broadcom'un dinleyeceği kanal. auto = AX210'un kanalı, 20 MHz | Özel deney |
| TX / sondalama hızı (`txRatePps`) ⟳ | 50 | Saniyedeki çerçeve sayısı = CSI ölçüm hızı | 30–80 arası; düşük = az veri, yüksek = daha çok hava trafiği |
| Sondalama hedefi (`probeTarget`) ⟳ | auto | Yalnız ax210_ba: ping hedefi (auto = modem) | — |
| TX yük boyu (`txPayloadBytes`) ⟳ | 600 | ap_udp/inject çerçeve boyu | — |
| Enjeksiyon kanalı / hızı ⟳ | 36 / 6 Mbps | Yalnız inject | — |
| Frame-control filtresi (`fcFilter`) | auto | Hangi çerçeve türlerinin işleneceği (auto = yönteme göre doğru tür) | Değiştirmeyin |

**Ön işleme**

| Ayar | Varsayılan | Ne işe yarar | Ne zaman değiştirilir |
|---|---|---|---|
| Ton grubu sayısı (`toneGroups`) | 56 | Şekil ve statik hesaplarında komşu tonlar bu kadar gruba ortalanır (gürültü azalır). 20 MHz'de en fazla 48 ton var | Değişirse yeniden kalibrasyon |
| PHY modu eşleşme korelasyonu (`modeMatch`) | 0,90 | Bir çerçevenin bilinen bir şekil moduna "ait" sayılması için gereken benzerlik (0–1) | Çok mod oluşuyorsa biraz düşürün |
| En fazla mod sayısı (`maxModes`) | 6 | Öğrenilebilecek farklı şekil modu sayısı | — |
| Faz ağırlığı (`phaseWeight`) | 0,5 | Statik/durum karşılaştırmasında fazın genliğe göre ağırlığı (0 = yalnız genlik) | — |
| Karar aralığı (`hopS`) | 0,5 sn | Kaç saniyede bir karar verildiği | — |

**Kalibrasyon.** Eşik formülü: **eşik = max(medyan + k × 1,4826 × MAD, yüzdelik × pay)**.

| Ayar | Varsayılan | Ne işe yarar |
|---|---|---|
| Kalibrasyon süresi (`calibrationS`) | 180 sn | Boş oda ölçüm süresi. 180–300 önerilir |
| Robust eşik katsayısı (`calMadK`) | 8 | Formüldeki k. **MAD**: değerlerin medyandan tipik uzaklığı; aykırı değerlerden etkilenmeyen bir "yayılma" ölçüsü |
| Eşik yüzdeliği (`calPercentile`) | 99,5 | Boş odada değerlerin %99,5'inin altında kaldığı nokta |
| Yüzdelik payı (`calMargin`) | 1,2 | O noktanın %20 üstü |

**Hareket**

| Ayar | Varsayılan | Ne işe yarar | Ne zaman değiştirilir |
|---|---|---|---|
| Hareket algılama (`motionEnabled`) | açık | Hareket dedektörünü açar | — |
| Hareket penceresi (`motionWindowS`) | 2 sn | Şekil dalgalanma ölçütünün baktığı süre | — |
| **Eşik çarpanı** (`motionScale`) | 1,0 | Bütün hareket eşiklerini çarpar: <1 daha hassas, >1 daha az hassas | **Oda için 1,25–1,5 önerilir** (§8.3) |
| Mutlak alt eşik (`motionMinThr`) | 0,002 | Şekil ölçütünün eşiği bundan küçük olamaz | — |
| Yenilik skoru (`noveltyEnabled`) | açık | Bilinen modlara benzemeyen çerçeve oranını da kanıt sayar | — |
| Ortak-mod güç kanalı (`motionPowerEnabled`) | kapalı | Çerçeve gücündeki ani değişimleri kanıt sayar. Verici güç sıçramalarına duyarlı | Deneysel |
| Kazanç/verici durumu geçişi (`gainStateEnabled`) + tolerans / üst sınır / alt eşik | açık; 2 dB / 12 dB / 0,03 | Yalnız ax210_ba'da: RSSI durum geçiş payı | ax210_action'da zaten etkisiz |
| **Sızıntı iptalli değişim T²** (`residualEnabled`) | açık | §7'deki asıl yöntem | Kapatmayın |
| T² pencere W (`residualWindowS`) | 3 sn | "Son" pencerenin uzunluğu. Kısa = hızlı ama gürültülü | — |
| T² taban B (`residualBaseS`) | 20 sn | Karşılaştırılan geçmiş. Durum değişiminden sonra bu kadar beklenir | — |
| Sızıntı referansı izleme τ (`residualTauS`) | 30 sn | Sızıntı vektörünün yavaş değişimleri izleme hızı | — |
| **T² alt eşik** (`residualMinThr`) | 2,0 | T² eşiği bundan küçük olamaz (boş odada beklenen ≈1) | Yanlış alarm varsa 2,5 |
| Tetik K / N (`motionOnK` / `motionOnN`) | 3 / 4 | "Son N kararın K'sı eşik üstündeyse alarm" | — |
| Bitiş için ardışık sakin karar (`motionOffCount`) | 4 | Alarm, bu kadar sakin karardan sonra biter | — |

**Statik değişim**

| Ayar | Varsayılan | Ne işe yarar |
|---|---|---|
| Statik değişim algılama (`staticEnabled`) | açık | Kalıcı değişim dedektörü |
| Statik pencere (`staticWindowS`) | 4 sn | Ortamın "şimdiki imzası" bu kadar sürenin ortalamasıdır |
| Eşik çarpanı (`staticScale`) | 1,0 | <1 daha hassas |
| Asgari z-mesafe eşiği (`staticMinDist`) | 3,0 | Statik eşik bundan küçük olamaz |
| Gereken sessizlik (`staticQuietS`) | 2 sn | Statik karar ancak bu kadar süre hareket yokken verilir (hareket kalıcı değişim sanılmasın) |
| Referans uyarlama τ (`staticAdaptTauS`) | 180 sn | Oda sakin ve normalken referans, sıcaklık ve kayma etkilerini bu hızla izler (0 = kapalı) |
| Kurulumda referansı tazele (`rebaselineOnStart`) | açık | Başlatınca ya da kurunca ilk sessiz an yeni statik referans olur |
| Otomatik yeni referans (`staticAutoRelearnS`) | 0 (room: 900) | Statik alarm bu kadar saniye sürerse yeni durum "normal" kabul edilir (0 = asla) |
| Tetik K / N, Bitiş | 4 / 6, 6 | Hareket dedektöründeki gibi |

**Durum profilleri**

| Ayar | Varsayılan | Ne işe yarar |
|---|---|---|
| Durum sınıflandırma (`stateEnabled`) | açık | Kayıtlı profillerle karşılaştırma |
| Alarm etiketleri (`stateAlarmLabels`) | `open,acik` (room: boş) | Bu durumlardan birine geçince alarm |
| Bilinmeyen eşiği (`openSetFactor`) | 2,0 × p99 | En yakın profile uzaklık, o profilin kendi tipik uzaklığının 2 katını aşarsa "bilinmeyen" |
| Karar marjı (`stateMargin`) | 0,1 | En yakın iki profil birbirine çok yakınsa karar verilmez ("belirsiz") |
| Kararlılık K / N | 3 / 4 | Durum değişikliğinin kabulü için |

**Mikro-hareket (deneysel)**

| Ayar | Varsayılan | Ne işe yarar |
|---|---|---|
| Mikro-hareket (`microEnabled`) | kapalı (vehicle_cargo: açık) | Nefes gibi yavaş, düzenli değişimleri arar |
| Pencere (`microWindowS`) | 20 sn | Frekans analizi için uzun pencere |
| Bant alt / üst (`microBandLowHz` / `microBandHighHz`) | 0,15 / 0,6 Hz | Nefes hızı aralığı (dakikada 9–36 nefes) |
| Eşik çarpanı, Tetik K / N | 1,0, 3 / 4 | — |

**Sinyal / sabotaj ve alarm**

| Ayar | Varsayılan | Ne işe yarar |
|---|---|---|
| Sinyal kaybı alarmı (`signalLossEnabled`) | açık | Ölçüm akışı kesilirse alarm |
| Asgari geçerli kare hızı (`minPacketRate`) | 5/sn | Bunun altı "sinyal yok" sayılır |
| Sinyal kaybı süresi (`signalLossS`) | 5 sn | Bu kadar sürerse alarm |
| RSSI düşüşü (`rssiDropEnabled`) | kapalı (vehicle_cargo: açık) | Sinyal gücü kalibrasyona göre düşerse alarm (anten kapatma, cihazı örtme) |
| RSSI düşüşü (dB) / süresi (sn) | 10 dB / 5 sn | Düşüş miktarı ve süresi |
| Aynı alarm için bekleme (`alarmCooldownS`) | 10 sn | Aynı türden alarmın en sık kaydedilme aralığı |
| Bırakma oranı (`releaseRatio`) | 0,9 | Alarm, skor eşiğin %90'ının altına inince bitmeye başlar (eşik etrafında titremeyi önler; **histerezis**) |

## 13. Oda krokisi ve yapılan deneyler

Ölçümler aşağıdaki odada yapıldı (ölçeksiz, kullanıcının çiziminden):

```text
                               ┌────────[ K: KAPI ]────────┐
                               │                    ░ salon (oda dışı)
                               │   kapı önü               │
                               │   ↕ hat_disi_uzak        │
 ┌─────────────────────────────┘   (kapı önü ↔ yatak ayağı)│
 │  M: MASA                 ◉ modem                       │
 │                                                         │
 │  S: SANDALYE  ← sandalye_otur_kalk (~1 m)               │
 ●  C: CİHAZ (Alpha, pencere dibinde)               ┌──────┤
 │  Y: YATAK                                        │  D:  │
 │     ✋ hat_disi_yakin: duvar dibinde el sallama   │ DOLAP│
 │        (~1,5 m)                                  │(ayna)│
 └──────────────────────────────────────────────────┴──────┘
```

- **C:** cihaz, yatağın baş tarafında duvarda.
- **Modem:** masanın sağ üst köşesinde, cihaza yaklaşık 2 m.
- **K:** kapı, odanın öbür ucunda, cihaza yaklaşık 4–5 m. Kapının dışı salon.
- **D:** sürgülü, aynalı dolap.

**Deneyler ve sonuçlar** (bağımsız, kurallar önceden sabit, §8.2):

| Etiket | Ne yapıldı | Konum | Sonuç |
|---|---|---|---|
| `sandalye_otur_kalk` | Sandalyeye oturup kalkma | ~1 m | 4/4 |
| `hat_disi_yakin` | Yatakta, duvar dibinde el sallama | ~1,5 m | 5/5 |
| `hareketsiz_insan_ayakta` | Ayakta, hareketsiz, yüz cihaza dönük, derin nefes (58 sn) | — | Sürenin %64'ünde alarm |
| `hat_disi_uzak` | Kapı önünden yatak ayağına kadar yürüyüp dönme | 2–4 m | 5/5 |
| `insan_girdi_cikti` | Odaya girip çıkma | 4–5 m → içeri | 4/5 |
| `kapi_acildi` / `kapi_kapandi` | Kapıyı açma / kapama | 4–5 m | 4/7 |
| `kapi_disi_hareket` | Kapı kapalıyken salonda, kapı önünde hareket | oda dışı | 1/5 (yakalanmaması istenir) |
| `bos_oda` | Oda boş, kapı kapalı | — | 15,5 dk'da 1 alarm |

**Krokiden çıkan dersler:**
- **Uzaklık azaldıkça yakalama artıyor:** yakın 10/10, orta 5/5, kapı 8/12. Bu, §7.4'teki fiziğin
  beklentisiyle uyumlu.
- **Kapı ve duvar, oda dışını büyük ölçüde ayırıyor** (1/5). Kapı açıkken bu ayrım zayıflıyor.
- **İlk denemelerdeki modem tuzağı:** modem masanın köşesinde olduğu için, cihaz–modem hattını
  kesen hareket (sandalye, kapıdan giriş) eski `ax210_ba` yönteminde çok güçlü görünüyordu. Bu
  yöntem ise tam olarak bu bağlantıyı ölçüyordu.

## 14. Hızlı başvuru sözlüğü

| Terim | Kısa açıklama |
|---|---|
| **ACK** | "Aldım" onay çerçevesi. |
| **AGC** | Otomatik kazanç kontrolü; alıcının sinyal seviyesini kendi kendine ayarlaması. |
| **Alt taşıyıcı** | Wi-Fi'nin yan yana kullandığı küçük frekanslardan biri ("piyano tuşu"). |
| **Artık** | Ölçümün, öğrenilmiş sızıntıyla açıklanamayan kısmı. Hareket bilgisi burada. |
| **AX210** | Intel'in Wi-Fi kartı. Burada hem ağ bağlantısı hem yayın yapan taraf. |
| **Bant genişliği** | Kanalın kapladığı frekans aralığı (burada 20 MHz). Ne kadar genişse mesafe ayrımı o kadar iyi. |
| **Beyazlatma** | Gürültüyü her yönde aynı boya getiren yeniden ölçekleme. |
| **Bistatik / monostatik** | Yayıncı ile dinleyici ayrı yerde / aynı yerde. |
| **BlockAck** | Wi-Fi'de "şu paketleri aldım" çerçevesi. İlk denemede kullanıldı; modem tuzağına yol açtı. |
| **Chanspec** | Kanal ve genişlik yazımı, ör. `48/20` = kanal 48, 20 MHz. |
| **CPE** | Her çerçevede bütün alt taşıyıcıların fazının birlikte rastgele dönmesi. |
| **CSI** | Kanal durum bilgisi: her alt taşıyıcı için genlik ve faz. |
| **Çerçeve** | Wi-Fi'nin havada gönderdiği tek bir veri paketi. |
| **Çok yollu yayılım** | Sinyalin alıcıya doğrudan ve yansımalarla birçok yoldan ulaşması. |
| **dB** | Güç oranlarının logaritmik ölçüsü (10 dB = 10 kat). |
| **dBm** | Mutlak güç birimi; 0 dBm = 1 miliwatt. −20 dBm, −50 dBm'den güçlüdür. |
| **Doppler** | Hareket eden yansıtıcının frekansta yarattığı küçük kayma. |
| **Eşik** | T²'nin "alarm" sayılması için geçmesi gereken değer. |
| **Faz** | Dalganın hangi noktasında ölçüldüğü ("ne zaman"). |
| **Gecikme p50 / p90** | Olayların %50'sinde / %90'ında alarmın en geç bu sürede geldiği değer. |
| **Genlik** | Sinyalin gücü ("ne kadar yüksek"). |
| **Hertz (Hz)** | Saniyedeki tekrar sayısı. 0,25 Hz = 4 saniyede bir. |
| **Histerezis** | Alarmın eşik etrafında açılıp kapanmasını önlemek için açılma ve kapanmaya farklı koşullar konması. |
| **Hotelling T²** | İki grup çok boyutlu ölçümün ortalamasının farklı olup olmadığını soran test. |
| **Hotspot** | Cihazın kendi Wi-Fi ağını yayınlaması. |
| **İzdüşüm (projeksiyon)** | Bir vektörün başka bir vektör yönündeki "gölgesi". |
| **Kalibrasyon** | Oda boşken sızıntıyı, gürültüyü ve eşiği öğrenme süreci. |
| **Karmaşık sayı** | Hem uzunluk (genlik) hem yön (faz) taşıyan sayı; bir ok gibi. |
| **Kovaryans** | Çok sayıdaki ölçümün ne kadar ve birlikte nasıl oynadığını özetleyen tablo. |
| **K-of-N** | "Son N kararın en az K'sı eşik üstündeyse" kuralı. |
| **Ledoit–Wolf** | Az veriyle kovaryansı güvenli tahmin etme yöntemi. |
| **MAD** | Medyan mutlak sapma; aykırı değerlere dayanıklı yayılma ölçüsü. |
| **mDNS (`csi-alpha.local`)** | Yerel ağda cihaza IP yerine adla ulaşma. |
| **Medyan** | Sıralanmış değerlerin ortasındaki değer. |
| **Nexmon** | Broadcom yongasını CSI ölçebilir hale getiren yazılım yaması. |
| **OFDM** | Bilgiyi birçok alt taşıyıcıya bölerek gönderme tekniği. |
| **PCA-1** | Çok sayıda sinyalin en çok birlikte değiştiği yön; hepsini özetleyen tek sinyal. |
| **PHY modu** | Çerçevenin gönderim biçimi (hız/akış); CSI'da farklı "şekil" bırakır. |
| **pps** | Saniyedeki paket (çerçeve) sayısı. |
| **Profil** | Ortamın kaydedilmiş bir "normal" hali (ör. kapı açık). |
| **Raspberry Pi** | Kart boyutunda küçük bilgisayar. |
| **Referans** | Statik dedektörün "normal ortam" imzası. |
| **RSSI** | Kartın verdiği tek sayılık sinyal gücü göstergesi. |
| **Sızıntı** | Yayıncının sinyalinin odaya uğramadan doğrudan dinleyiciye gitmesi. |
| **Skor** | Ölçülen değer ÷ eşik; 1 (%100) üstü eşik aşımı. |
| **Spektrogram / STFT** | Bir sinyalin hangi hızlarda dalgalandığının zamana göre gösterimi. |
| **SSID** | Wi-Fi ağının adı. |
| **STO** | Saatler arası küçük zaman kayması; alt taşıyıcılar boyunca faz eğimi yaratır. |
| **Yakalama (recall)** | Gerçek olayların alarm üretilerek fark edilen oranı. |
| **Yanlış alarm** | Ortada olay yokken çalan alarm. |
| **Yeniden gönderim (retry)** | Karşı taraf almadığında aynı çerçevenin tekrar yollanması. |
| **Yüzdelik (persentil)** | Değerlerin belli bir yüzdesinin altında kaldığı nokta (ör. P99,5). |
| **z-mesafe** | Bir ölçümün "normal"den, gürültü birimiyle (standart sapma) ne kadar uzak olduğu. |

## 15. Daha fazlası için

- `docs/tek-kutu-algilama-tr.md`: sistemin teknik belgesi, bütün deneyler ve ölçümler.
- `docs/csi-algoritma-matematik-tr.md` §22: matematiğin türetmeleri ve sayısal doğrulamaları.
- `data/csi/reports/single_box_bench_2026-11-09.md`: karşılaştırma, ablasyon ve eşik taraması tabloları.
- `tools/csi_sensing/bench/README.md`: tabloları yeniden üretme adımları.
