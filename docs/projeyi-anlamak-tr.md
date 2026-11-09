# Projeyi Detaylarıyla Anlamak

*Wi-Fi sinyaliyle, kamera kullanmadan, tek bir küçük bilgisayarın odadaki hareketi nasıl fark ettiğinin
baştan sona anlatımı.*

Bu belge konuya hiç yabancı olmayan biri için yazıldı. Önce kavramları gündelik benzetmelerle
anlatıyor, sonra sistemi, sonra matematiğini, en sonda da sonuçları ve sınırları. Matematik
bölümlerinde her sembolün ne anlama geldiği yazılıdır; formülleri atlayıp yalnız açıklamaları okumak
da bütün resmi anlamaya yeter. Teknik ayrıntının tamamı için §12'deki belgelere bakın.

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

## 11. Hızlı başvuru sözlüğü

| Terim | Kısa açıklama |
|---|---|
| **AGC** | Otomatik kazanç kontrolü; alıcının sinyal seviyesini kendi kendine ayarlaması. |
| **Alt taşıyıcı** | Wi-Fi'nin yan yana kullandığı küçük frekanslardan biri ("piyano tuşu"). |
| **Artık** | Ölçümün, öğrenilmiş sızıntıyla açıklanamayan kısmı. Hareket bilgisi burada. |
| **AX210** | Intel'in Wi-Fi kartı. Burada hem ağ bağlantısı hem yayın yapan taraf. |
| **Bant genişliği** | Kanalın kapladığı frekans aralığı (burada 20 MHz). Ne kadar genişse mesafe ayrımı o kadar iyi. |
| **Beyazlatma** | Gürültüyü her yönde aynı boya getiren yeniden ölçekleme. |
| **Bistatik / monostatik** | Yayıncı ile dinleyici ayrı yerde / aynı yerde. |
| **BlockAck** | Wi-Fi'de "şu paketleri aldım" çerçevesi. İlk denemede kullanıldı; modem tuzağına yol açtı. |
| **CPE** | Her çerçevede bütün alt taşıyıcıların fazının birlikte rastgele dönmesi. |
| **CSI** | Kanal durum bilgisi: her alt taşıyıcı için genlik ve faz. |
| **Çerçeve** | Wi-Fi'nin havada gönderdiği tek bir veri paketi. |
| **Çok yollu yayılım** | Sinyalin alıcıya doğrudan ve yansımalarla birçok yoldan ulaşması. |
| **dB** | Güç oranlarının logaritmik ölçüsü (10 dB = 10 kat). |
| **Doppler** | Hareket eden yansıtıcının frekansta yarattığı küçük kayma. |
| **Eşik** | T²'nin "alarm" sayılması için geçmesi gereken değer. |
| **Faz** | Dalganın hangi noktasında ölçüldüğü ("ne zaman"). |
| **Genlik** | Sinyalin gücü ("ne kadar yüksek"). |
| **Hotelling T²** | İki grup çok boyutlu ölçümün ortalamasının farklı olup olmadığını soran test. |
| **İzdüşüm (projeksiyon)** | Bir vektörün başka bir vektör yönündeki "gölgesi". |
| **Kalibrasyon** | Oda boşken sızıntıyı, gürültüyü ve eşiği öğrenme süreci. |
| **Karmaşık sayı** | Hem uzunluk (genlik) hem yön (faz) taşıyan sayı; bir ok gibi. |
| **Kovaryans** | Çok sayıdaki ölçümün ne kadar ve birlikte nasıl oynadığını özetleyen tablo. |
| **Ledoit–Wolf** | Az veriyle kovaryansı güvenli tahmin etme yöntemi. |
| **Nexmon** | Broadcom yongasını CSI ölçebilir hale getiren yazılım yaması. |
| **OFDM** | Bilgiyi birçok alt taşıyıcıya bölerek gönderme tekniği. |
| **Raspberry Pi** | Kart boyutunda küçük bilgisayar. |
| **RSSI** | Kartın verdiği tek sayılık sinyal gücü göstergesi. |
| **Sızıntı** | Yayıncının sinyalinin odaya uğramadan doğrudan dinleyiciye gitmesi. |
| **STO** | Saatler arası küçük zaman kayması; alt taşıyıcılar boyunca faz eğimi yaratır. |
| **Yanlış alarm** | Ortada olay yokken çalan alarm. |
| **Yakalama (recall)** | Gerçek olayların alarm üretilerek fark edilen oranı. |
| **Yeniden gönderim (retry)** | Karşı taraf almadığında aynı çerçevenin tekrar yollanması. |

## 12. Daha fazlası için

- `docs/tek-kutu-algilama-tr.md`: sistemin teknik belgesi, bütün deneyler ve ölçümler.
- `docs/csi-algoritma-matematik-tr.md` §22: matematiğin türetmeleri ve sayısal doğrulamaları.
- `data/csi/reports/single_box_bench_2026-11-09.md`: karşılaştırma, ablasyon ve eşik taraması tabloları.
- `tools/csi_sensing/bench/README.md`: tabloları yeniden üretme adımları.
