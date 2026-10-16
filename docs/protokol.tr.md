# Holix Yerel Denetim Protokolü (HLCP) — sürüm 1

> English version: [protocol.md](protocol.md). İki sürüm aynı içeriktedir; çelişkide İngilizce sürüm esastır.

Holix Play uygulamasının (Apple TV, iPhone/iPad, Mac, Android/Android TV, ileride HarmonyOS, Windows)
yerel ağda açtığı denetim ucu. İlk istemci Home Assistant entegrasyonu (`custom_components/holix`);
aynı uç ileride başka ev sistemleri ve Holix'in kendi kumandaları için kullanılabilir.

Bu belge **bağlayıcı sözleşmedir**. Makine-okunur şema: [`protocol.schema.json`](protocol.schema.json)
(JSON Schema 2020-12). Uygunluk denetimi: `tools/holix_probe.py` (bkz. §10).
Anahtar sözcükler (MUST/SHOULD/MAY) RFC 2119 anlamındadır; Türkçe metinde "ZORUNLU / GEREKİR / OLABİLİR".

## 1. Roller ve taşıma

- **Cihaz (sunucu):** Holix Play uygulaması. Yalnız uygulama **ön plandayken** dinler (tvOS/iOS arka
  planda süreç askıya alınır; uygulama arka plana geçerken bağlantıları `4002` ile kapatır, mDNS ilanını
  geri çeker, ön plana dönünce yeniden açar).
- **İstemci:** Home Assistant (ya da `holix_probe`).
- Taşıma: WebSocket (RFC 6455) üzerinden düz TCP, yol **`/holix/v1`**. Varsayılan port **47861**;
  port doluysa cihaz başka port seçer ve mDNS'te ilan eder (istemci portu hep mDNS/ayardan alır).
- Her çerçeve tek bir UTF-8 JSON nesnesi (text frame). İkili çerçeve kullanılmaz.
- Her nesnede `type` (string) ZORUNLU. Bilinmeyen alanlar her iki tarafta **yok sayılır** (ileri uyum).
  Bilinmeyen `type` cihazda `unknown_type` hatasıyla yanıtlanır (`id` varsa), istemcide yok sayılır.
- Eşzamanlı en az **4** yetkili bağlantı desteklenir; durum ve olaylar hepsine yayınlanır.
- Canlılık: istemci 30 sn'de bir WebSocket ping çerçevesi gönderir (cihaz WS kütüphanesi pong döner).
  Uygulama düzeyinde `ping` → `pong` da desteklenir. Cihaz 90 sn boyunca hiçbir çerçeve almazsa
  bağlantıyı kapatabilir.
- TLS sürüm 1'de yok (yerel ağ). Belirteç ağda **asla** düz gönderilmez — yalnız eşleştirmede bir kez
  (bkz. §4, kısıt §11).

## 2. Keşif (mDNS / DNS-SD)

- Hizmet türü: **`_holixplay._tcp.local.`**
- Örnek adı: kullanıcının cihaz adı (ör. `Living Room Apple TV`), en fazla 63 bayt.
- SRV: cihazın IP'si ve dinlediği port.
- TXT kayıtları (hepsi ZORUNLU, anahtarlar küçük harf):

| Anahtar | Örnek | Anlam |
|---|---|---|
| `id` | `5b0c1d9e-3f0a-4c55-9a43-1f0e2b7a9c10` | Kalıcı cihaz kimliği (UUID v4, kurulumda üretilir, Keychain/SharedPreferences'ta saklanır; yeniden kurulumda değişebilir). HA `unique_id`'si budur. |
| `proto` | `1` | Protokol ana sürümü. |
| `path` | `/holix/v1` | WebSocket yolu. |
| `platform` | `tvos` | `tvos` `ios` `ipados` `macos` `visionos` `android` `androidtv` `harmonyos` `windows` |
| `model` | `AppleTV14,1` | Donanım modeli (Apple: `hw.machine`; Android: `Build.MODEL`). |
| `ver` | `0.4.0` | Uygulama sürümü. |

Apple: `Info.plist` → `NSBonjourServices = ["_holixplay._tcp"]`, `NSLocalNetworkUsageDescription`.
Dinleme `NWListener` (`service: NWListener.Service(name:type:"_holixplay._tcp", txtRecord:)`).
Android: `NsdManager.registerService` (`serviceType = "_holixplay._tcp"`), ağ izni `INTERNET`.

## 3. Mesaj zarfları

| Yön | `type` | Alanlar |
|---|---|---|
| C→İ | `hello` | bağlanınca ilk mesaj, istek beklemeden |
| İ→C | `pair_start`, `pair_finish`, `auth`, `command`, `subscribe_colors`, `unsubscribe_colors`, `ping` | hepsinde `id` (pozitif tamsayı, bağlantı içinde artan, istemci üretir) |
| C→İ | `result` | her istek için **tam bir** yanıt, aynı `id` |
| C→İ | `state`, `event`, `colors` | yalnız yetkili bağlantıya, istek olmadan |

(C = cihaz, İ = istemci.)

**result:**
```json
{"type": "result", "id": 7, "success": true, "result": {}}
{"type": "result", "id": 7, "success": false, "error": {"code": "invalid_args", "message": "position < 0"}}
```
`result` nesnesi başarıda ZORUNLU (boş olabilir). `message` insan için; istemci yalnız `code`'a bakar.

**Hata kodları:** `unauthorized` (yetkisiz bağlantıda yetki isteyen mesaj), `invalid_auth`,
`invalid_pin`, `pairing_not_started`, `too_many_attempts`, `unknown_type`, `unknown_command`,
`invalid_args`, `not_supported` (cihaz/platform yapamıyor, ör. tvOS'ta ses düzeyi),
`not_available` (şu an uygulanamaz, ör. oynatma yokken `seek`), `internal`.

**Kapatma kodları (WebSocket close):** `4000` protokol hatası, `4001` yetki geri alındı (kullanıcı
uygulamada eşleşmeyi sildi → istemci yeniden eşleştirme ister), `4002` uygulama arka plana geçiyor /
kapanıyor (istemci yeniden bağlanmayı dener), `4003` bağlantı sınırı aşıldı.

## 4. Merhaba, eşleştirme, yetkilendirme

### 4.1 hello (cihaz → istemci, bağlanır bağlanmaz)
```json
{
  "type": "hello",
  "proto": 1,
  "device": {"id": "5b0c1d9e-…", "name": "Living Room Apple TV", "model": "AppleTV14,1",
             "platform": "tvos", "app_version": "0.4.0 (57)"},
  "nonce": "q0n3eW5yZ0ZzYk1sT0dtVg",
  "capabilities": ["playback", "profiles", "ambilight", "colors", "notify", "keys"]
}
```
- `nonce`: bağlantı başına **yeni**, en az 16 rastgele bayt, base64url (dolgusuz).
- `capabilities` (cihaz ne destekliyorsa): `playback` (durum + oynatma komutları), `volume`
  (`set_volume`/`mute` uygulanabilir), `profiles`, `ambilight` (Holix ambilight çıkışı var),
  `colors` (renk akışı, §7), `notify` (ekranda bildirim), `keys` (gezinme tuşları), `play_item`.
- `proto` istemcinin bildiğinden büyükse istemci bağlantıyı kapatır ve "uygulama daha yeni" der.

### 4.2 Eşleştirme (bir kez; PIN ekranda)
```json
→ {"type": "pair_start", "id": 1, "client_id": "2d8f…", "client_name": "Home Assistant (Ev)"}
← {"type": "result", "id": 1, "success": true, "result": {"pin_length": 6, "expires_in": 120}}
```
Cihaz ekranda 6 haneli PIN ve `client_name`'i gösterir (TV'de tam ekran kart, iPhone'da sayfa).
PIN `0–9`, 120 sn geçerli, aynı anda tek eşleştirme (yeni `pair_start` eskisini geçersiz kılar).
```json
→ {"type": "pair_finish", "id": 2, "client_id": "2d8f…", "pin": "482913"}
← {"type": "result", "id": 2, "success": true, "result": {"token": "Zk8x…43 karakter"}}
```
- `token`: 32 rastgele bayt, base64url (dolgusuz) → 43 karakter. Cihaz `client_id → token` saklar
  (Keychain / EncryptedSharedPreferences). Aynı `client_id` yeniden eşleşirse eski belirteç silinir.
- Hatalar: `pairing_not_started` (yok/süresi doldu/farklı `client_id`), `invalid_pin` (kalan hak
  `result` değil `error.message`'da), 5. yanlışta `too_many_attempts` ve eşleştirme iptal.
- Eşleştirme sonrası bağlantı hâlâ **yetkisizdir**; istemci aynı bağlantıda `auth` gönderir.
- Uygulama ayarlarında "Bağlı ev sistemleri" listesi: ad, eşleşme tarihi, son bağlantı, **Kaldır**
  (→ belirteç silinir, açık bağlantı `4001` ile kapanır).

### 4.3 auth (her bağlantıda)
```json
→ {"type": "auth", "id": 3, "client_id": "2d8f…", "hmac": "<64 hex>"}
← {"type": "result", "id": 3, "success": true, "result": {}}
← {"type": "state", "state": {…}}          // hemen ardından tam durum
```
`hmac = hex(HMAC-SHA256(key = UTF-8(token), msg = UTF-8(nonce)))`, küçük harf. Belirteç ağda
dolaşmaz; `nonce` bağlantıya özel olduğundan tekrar oynatma işe yaramaz. Karşılaştırma sabit
zamanlı yapılmalı. Hata: `invalid_auth` (bilinmeyen `client_id` ya da yanlış HMAC) → istemci
yeniden eşleştirme akışına geçer. Yetkisiz bağlantı 30 sn içinde `auth`/eşleştirme yapmazsa
cihaz kapatabilir. Yetkisiz bağlantıda yalnız `pair_start`, `pair_finish`, `auth`, `ping` kabul;
gerisi `unauthorized`.

Test vektörü: token `dGVzdC10b2tlbi0wMDAwMDAwMDAwMDAwMDAwMDAwMDA`, nonce `bm9uY2UtMDAwMDAwMDAwMA`
→ hmac `c607ad23048f0e5684d11cd65c51c128405a1e20d6615ce89af5b1cc2cea5c7d`
(`tests/test_protocol.py`'de doğrulanır; çekirdek/istemci testlerine aynen alınır).

## 5. Durum (`state`)

Yetkili bağlantıya: `auth` başarısından hemen sonra ve **her anlamlı değişimde** tam anlık görüntü
(yama yok — nesne küçük). Oynatma konumu sürekli gönderilmez: `position` + `position_at` (o konumun
ölçüldüğü an, Unix ms) + `rate` ile istemci kendisi ilerletir. Cihaz `state`'i şu anlarda gönderir:
oynatma durumu, öğe, profil, ses/altyazı izi, segment, ambilight, ekran koruyucu değişince, seek'te
ve oynatma sürerken en az 60 sn'de bir. Sıklık en fazla 4/sn (yığılırsa son durum gönderilir).

```json
{
  "type": "state",
  "state": {
    "playback": {
      "state": "playing",
      "position": 1834.2, "duration": 7421.0, "position_at": 1792152000123, "rate": 1.0,
      "item": {
        "id": "a1b2c3d4e5f60718293a4b5c6d7e8f90", "type": "episode",
        "title": "Ozymandias", "series_title": "Breaking Bad", "season": 5, "episode": 14,
        "year": 2013, "official_rating": "TV-MA",
        "image_url": "https://jf.example.net/Items/a1b2…/Images/Primary?maxWidth=600&tag=…"
      },
      "video": {"dynamic_range": "dolby_vision", "width": 3840, "height": 2160,
                "codec": "hevc", "frame_rate": 23.976},
      "audio": {"codec": "eac3", "channels": 6, "spatial": true, "language": "eng"},
      "subtitle": {"language": "tur", "forced": false},
      "segment": null,
      "play_method": "direct_play",
      "volume": null, "muted": false
    },
    "profile": {"id": "p-alex", "name": "Alex", "kind": "adult"},
    "profiles": [
      {"id": "p-alex", "name": "Alex", "kind": "adult"},
      {"id": "p-kids", "name": "Kids", "kind": "child"},
      {"id": "p-guest", "name": "Guest", "kind": "guest"}
    ],
    "ambilight": {"on": true, "brightness": 0.8, "targets": 2},
    "screensaver": false
  }
}
```

| Alan | Tür | Kural |
|---|---|---|
| `playback.state` | `idle` `playing` `paused` `buffering` | `idle` = oynatıcı kapalı (göz atma/menü) |
| `position`, `duration` | sayı (sn) \| null | `idle`'da null; canlı TV'de `duration` null |
| `position_at` | tamsayı (Unix ms) \| null | `position` ölçüm anı (cihaz saati; NTP'li varsayılır) |
| `rate` | sayı | duraklatılmışken 0 değil, son hız; istemci `state=="playing"` iken ilerletir |
| `item` | nesne \| null | `idle`'da null |
| `item.id` | string | Jellyfin `ItemId` (32 hex) |
| `item.type` | `movie` `episode` `trailer` `music_video` `live_tv` `audio` `video` `other` | |
| `item.series_title` `season` `episode` `year` `official_rating` `image_url` | \| null | `image_url` mutlak URL; Jellyfin `api_key` **içermez** (genel önbellek etiketiyle) |
| `video.dynamic_range` | `sdr` `hdr10` `hdr10_plus` `dolby_vision` `hlg` | **ekrana çıkan** biçim (DV→HDR10 düşüşünde `hdr10`) |
| `video.codec` / `audio.codec` | string | Jellyfin codec adları (`hevc`, `av1`, `h264`, `eac3`, `truehd`, `aac` …) |
| `audio.spatial` | bool | Atmos / DTS:X / uzamsal ses etkin |
| `audio.language` `subtitle.language` | ISO 639-2 \| null | |
| `subtitle` | nesne \| null | null = altyazı kapalı |
| `segment` | `intro` `outro` `recap` `preview` `commercial` \| null | Jellyfin medya segmenti, şu an içindeyse |
| `play_method` | `direct_play` `direct_stream` `transcode` \| null | |
| `volume` | 0–1 \| null | null = uygulama ses düzeyi yönetmiyor (tvOS, HDMI-CEC) |
| `profile` | nesne \| null | null = profil seçme ekranı |
| `profile.kind` | `adult` `child` `guest` | |
| `profiles` | dizi | PIN'li profiller dahil; PIN'li profile geçiş komutu `not_available` döner (§6) |
| `ambilight` | nesne \| null | null = cihazda ambilight yok/yapılandırılmamış; `targets` = bağlı LED denetleyici sayısı |
| `screensaver` | bool | uygulama içi ekran koruyucu (photoframe) açık |

## 6. Komutlar

```json
→ {"type": "command", "id": 9, "command": "seek", "args": {"position": 600}}
← {"type": "result", "id": 9, "success": true, "result": {}}
```
`args` her zaman nesne (boş olabilir, yoksa `{}` sayılır). Komut başarılıysa yeni durum ayrıca
`state` ile gelir; `result` durum taşımaz.

| `command` | `args` | Not / hatalar |
|---|---|---|
| `play` | — | `not_available` (öğe yok) |
| `pause` | — | |
| `play_pause` | — | |
| `stop` | — | oynatıcıyı kapatır → `idle` |
| `next` / `previous` | — | sonraki/önceki bölüm ya da sıra öğesi |
| `seek` | `position` (sn, ≥0) | `invalid_args` |
| `set_volume` | `level` (0–1) | `not_supported` (`volume` yeteneği yoksa) |
| `mute` | `muted` (bool) | `not_supported` |
| `select_profile` | `profile_id` | `invalid_args` (yok), `not_available` (PIN'li ya da oynatma sürüyor) |
| `set_ambilight` | `on`? (bool), `brightness`? (0–1) | en az biri; `not_supported` |
| `play_item` | `item_id` (Jellyfin), `position`? (sn) | etkin profil adına oynatır; `not_available` (profil yok/yaş sınırı) |
| `notify` | `message`, `title`?, `duration`? (sn, 1–60, vars. 8) | ekranda tost; oynatmayı durdurmaz |
| `key` | `key`: `up` `down` `left` `right` `select` `back` `home` `menu` `play_pause` | uygulama içi gezinme |

Yaş sınırı ve PIN kuralları komutla **aşılamaz** (çocuk profilinde `play_item` yetişkin içerik →
`not_available`).

## 7. Olaylar (`event`)

```json
{"type": "event", "event": "playback_started", "ts": 1792152000123,
 "data": {"item": {…}, "video": {…}, "audio": {…}, "profile": {…}}}
```

| `event` | `data` | Ne zaman |
|---|---|---|
| `playback_started` | `item`, `video`, `audio`, `profile` | yeni öğe oynamaya başladı (sıradaki bölüme geçiş dahil) |
| `playback_paused` | `item`, `position` | kullanıcı/komut duraklattı (buffering değil) |
| `playback_resumed` | `item`, `position` | |
| `playback_stopped` | `item`, `position`, `duration`, `reason` (`ended` `user` `error`) | |
| `segment_started` / `segment_ended` | `segment`, `item` | intro/outro/recap/preview/commercial sınırı (`outro` = jenerik → ışık aç) |
| `profile_changed` | `profile` (\| null) | |
| `screensaver_started` / `screensaver_stopped` | `{}` | |

Olay sırası: önce `state`, sonra `event` (istemci olayı işlediğinde durum güncel). `item`, `video`,
`audio`, `profile` §5'teki şemalarla aynıdır.

## 8. Renk akışı (`colors`) — HA ışıklarını ambilight hedefi yapmak

Holix ambilight'ın kendi LED yolu (DDP/WLED vb.) bundan bağımsızdır;
bu akış yalnız **Wi-Fi/Zigbee lambaları HA üzerinden** sürmek içindir ve düşük hızlıdır.
```json
→ {"type": "subscribe_colors", "id": 11, "fps": 4}
← {"type": "result", "id": 11, "success": true, "result": {"fps": 4}}
← {"type": "colors", "seq": 1, "ts": 1792152000123,
   "zones": {"left": [12, 30, 80], "right": [10, 28, 70], "top": [5, 5, 20],
             "bottom": [40, 30, 10], "center": [60, 60, 60], "average": [25, 30, 52]}}
→ {"type": "unsubscribe_colors", "id": 12}
```
- `fps` 1–30 istenir; cihaz `result.fps`'te gerçek hızı bildirir (ambilight kapalıyken de akış
  verebilir; video kenarından çıkarılır). `colors` yalnız oynatma sürerken gönderilir.
- Bölgeler sabit: `left` `right` `top` `bottom` (kenar şeritlerinin ortalaması), `center`, `average`;
  her biri `[r, g, b]` 0–255, sRGB, ambilight ayarlarındaki doygunluk/gama uygulanmış, **parlaklık
  ölçeklenmemiş**. `seq` bağlantı başına 1'den artar.
- `colors` bir abonelik yokken gönderilmez; bağlantı kopunca abonelik düşer.

## 9. Sürümleme

- Geriye uyumlu eklemeler (yeni alan, yeni olay, yeni komut, yeni yetenek) `proto` artırmaz.
- Bozucu değişiklikte `proto` 2 ve yol `/holix/v2`; cihaz bir süre ikisini birden sunar.

## 10. Uygunluk

- `docs/protocol.schema.json`: tüm mesajların şeması (`$defs` altında her `type`).
- `tools/holix_probe.py <host> [--port 47861]`: gerçek uygulamaya bağlanır, eşleştirir (PIN'i sorar),
  `auth` yapar, her gelen mesajı şemaya karşı doğrular, isteğe bağlı komutları dener. Platform
  ajanları uygulamayı bununla doğrular. Ayrıca `tests/fake_device.py` referans cihaz uygulamasıdır
  (Python, `aiohttp`): davranış belirsizse referans odur.
- Çekirdek/istemci testlerine §4.3 HMAC test vektörü ve `tests/fixtures/*.json` örnek mesajları alınır.

## 11. Güvenlik notları

- Eşleştirmede belirteç bir kez düz WebSocket'te gider; aynı anda ağı dinleyen saldırgan yakalayabilir.
  Kabul edilen risk (yerel ağ, ekranda PIN onayı). Sürüm 2 adayı: PIN'den PAKE (SPAKE2) ile anahtar.
- Komutlar oturum içinde ayrıca imzalanmaz; TCP oturumunu ele geçirmek LAN'da MITM gerektirir.
- Cihaz yalnız özel adreslerden (RFC 1918, ULA `fc00::/7`, link-local) gelen bağlantıları kabul ETMELİ.
- `image_url` ve olay verisi Jellyfin erişim belirteci, kullanıcı parolası ya da PIN İÇERMEZ.
