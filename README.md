# Marawa BPS Padang Pariaman

Chatbot WhatsApp Python untuk layanan data BPS Kabupaten Padang Pariaman. Bot menerima webhook dari GOWA, menjaga sesi percakapan, mencari data di BPS WebAPI domain `1306`, memakai AI sebagai parser aman, melakukan handoff admin, dan mencatat log ke Google Spreadsheet.

## Arsitektur

- `gowa`: WhatsApp gateway build dari fork [`alfathhh/go-whatsapp-web-multidevice`](https://github.com/alfathhh/go-whatsapp-web-multidevice) (submodule di `./gowa`).
- `marawa-bot`: FastAPI webhook receiver di `POST /webhook/gowa`.
- `app/conversation`: state machine, session store, timeout, parsing tahun/triwulan.
- `app/services`: adapter GOWA, BPS WebAPI, AI, Google Sheets, admin handoff.
- `GuardedDataAgent`: AI hanya boleh memilih aksi yang diizinkan aplikasi, bukan menjalankan tool bebas.

## GOWA — Build dari Fork vs Image Docker Hub

Saat ini GOWA di-build dari fork sendiri (submodule `./gowa`). Kalau mau ganti, edit `docker-compose.yml` bagian `gowa`:

### Opsi A: Build dari fork (default sekarang)

```yaml
gowa:
  build:
    context: ./gowa
    dockerfile: docker/golang.Dockerfile
```

Perlu clone submodule dulu:

```bash
git submodule update --init --recursive
docker compose up -d --build gowa
```

Kalau ada update di fork:

```bash
git submodule update --remote gowa
git add gowa && git commit -m "chore: update gowa"
docker compose up -d --build gowa
```

### Opsi B: Pakai image Docker Hub (upstream original)

Ganti di `docker-compose.yml`:

```yaml
gowa:
  image: aldinokemal2104/go-whatsapp-web-multidevice:latest
```

Lalu:

```bash
docker compose up -d --force-recreate gowa
```

> **Note:** Submodule `./gowa` tidak perlu di-clone kalau pakai Opsi B.

## Instalasi Dari Nol (dengan Build GOWA dari Fork)

Kalau lo clone repo ini dan mau jalankan dengan GOWA build dari fork (Opsi A di atas):

```bash
# 1. Clone repo beserta submodule GOWA
git clone --recurse-submodules https://github.com/alfathhh/marawav999.git
cd marawav999

# Atau kalau sudah clone tanpa submodule:
git submodule update --init --recursive
```

```bash
# 2. Salin konfigurasi
cp .env.example .env
# Edit .env sesuai kebutuhan
```

```bash
# 3. Build dan jalankan semua service
docker compose up -d --build
```

> **Catatan:** Build GOWA pertama kali akan lebih lama (~2-5 menit) karena perlu compile Go dari source.

Untuk update GOWA ke commit terbaru fork:

```bash
git submodule update --remote gowa
git add gowa && git commit -m "chore: update gowa"
docker compose up -d --build gowa
```

---

## Deployment & Update

Setelah ada perubahan kode (merge PR / pull dari GitHub):

```bash
# 1. Pull perubahan terbaru
git pull origin main

# 2. Rebuild dan restart bot
docker compose up -d --build marawa-bot
```

Kalau hanya ingin restart tanpa rebuild (misal ubah `.env`):

```bash
docker compose up -d --force-recreate marawa-bot
```

Untuk melihat log setelah update:

```bash
docker compose logs -f marawa-bot
```

## Instalasi Dari Nol

1. Masuk ke folder proyek:

```powershell
cd D:\Code\bps-marawa
```

2. Salin konfigurasi:

```powershell
Copy-Item .env.example .env
```

3. Isi `.env`.

Contoh minimum:

```env
BPS_API_KEY=isi_api_key_bps
BPS_DOMAIN=1306
BPS_CACHE_TTL_SECONDS=3600
BPS_CACHE_DB_PATH=/app/data/bps_cache.sqlite3

AI_PROVIDER=openai
OPENAI_API_KEY=isi_openai_key
OPENAI_MODEL=gpt-4o-mini
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_MODEL=llama3.1

GOWA_BASE_URL=http://gowa:3000
GOWA_BASIC_AUTH_USER=marawa
GOWA_BASIC_AUTH_PASS=isi_password
GOWA_WEBHOOK_SECRET=isi_secret_webhook

ADMIN_NUMBERS=628xxxx,628yyyy
BOT_PHONE_NUMBER=628zzzzzzz
SESSION_TIMEOUT_SECONDS=600
ADMIN_PICKUP_TIMEOUT_SECONDS=300

GOOGLE_SHEETS_SPREADSHEET_ID=isi_id_spreadsheet
GOOGLE_SERVICE_ACCOUNT_JSON=/app/secrets/gaut.json
```

4. Simpan file service account Google di:

```text
D:\Code\bps-marawa\app\secret\gaut.json
```

Di dalam container file ini akan terbaca sebagai:

```text
/app/secrets/gaut.json
```

5. Share Google Spreadsheet ke email service account.

Email service account ada di file JSON pada field `client_email`. Beri akses `Editor`.

6. Build dan jalankan:

```powershell
docker compose up --build
```

7. Buka GOWA:

```text
http://localhost:3000/app/login
```

Scan QR WhatsApp.

8. Cek health bot:

```text
http://localhost:8000/health
```

## Google Sheets

Spreadsheet boleh kosong. Bot akan mencoba membuat sheet dan header ini otomatis jika service account punya akses editor:

- `users`: nomor, nama WhatsApp, pertama kali interaksi, terakhir aktif, total sesi, status terakhir.
- `conversations`: timestamp, nomor, nama, arah pesan, state, intent, isi pesan, respons bot, metadata, URL sumber.

Kalau log belum masuk:

```powershell
docker compose logs -f marawa-bot
docker compose exec marawa-bot sh -lc "test -f /app/secrets/gaut.json && echo FOUND || echo MISSING"
```

Pastikan:

- `GOOGLE_SHEETS_SPREADSHEET_ID` benar.
- Spreadsheet sudah di-share ke `client_email`.
- `GOOGLE_SERVICE_ACCOUNT_JSON=/app/secrets/gaut.json`.
- File lokal ada di `app\secret\gaut.json`.

## Alur Chat

Setiap sesi baru, bot memperkenalkan diri dan menampilkan layanan:

1️⃣ Cari data statistik BPS Kab. Padang Pariaman
2️⃣ Rekomendasi & konsultasi statistik
3️⃣ Hubungi admin
4️⃣ Akhiri percakapan

Pada sesi baru, pesan pertama user selalu dibalas greeting/menu saja. Pesan pertama belum diproses sebagai permintaan data, admin, atau menu. Ini membuat percakapan aman setelah server/container restart karena semua session in-memory akan reset dan user selalu mulai dari awal lagi. Jika user tidak membalas sampai `SESSION_TIMEOUT_SECONDS`, bot otomatis mengirim pemberitahuan timeout dan sesi diakhiri.

> **Container restart = semua sesi reset.** User yang chat setelah restart akan mulai dari greeting/menu utama.

User boleh mengetik angka, kata kunci, atau kalimat natural.

Contoh alur data:

```text
User: data tpt
Bot: menampilkan beberapa pilihan data mirip
User: 1
Bot: meminta tahun
User: 2020-2025
Bot: menampilkan tabel tahun yang tersedia dan memberi catatan jika sebagian tahun belum tersedia
```

Jika hasil terlalu banyak, bot menampilkan 5 pilihan per halaman. User bisa mengetik:

- `lainnya` untuk halaman berikutnya.
- `sebelumnya` untuk halaman sebelumnya.
- nomor pilihan, misalnya `2`.
- kata kunci yang lebih detail.

Di akhir respons data, bot selalu menambahkan:

```text
Jika permintaan data tidak ditemukan, bisa masuk ke https://s.bps.go.id/tamu1306
```

## Cara Bot Mencari Data

Prioritas sumber data:

1. Tabel dinamis BPS WebAPI, model `var` dan `data`.
2. SIMDASI, model `statictable`.
3. Publikasi BPS, model `publication`.

Untuk topik luas seperti `ketenagakerjaan`, `ekonomi`, `penduduk`, `kesehatan`, atau `pendidikan`, bot memakai ekspansi keyword di [app/services/bps_client.py](D:/Code/bps-marawa/app/services/bps_client.py). Contohnya `ketenagakerjaan` diperluas menjadi `tenaga kerja`, `angkatan kerja`, `pengangguran`, `tpt`, `tpak`, dan lainnya.

Search pipeline terbaru:

1. AI/parser mengambil query utama, tahun, triwulan, dan beberapa variasi keyword.
2. Sistem menambah rewrite lokal untuk istilah awam dan singkatan, misalnya `kerjaan` -> `ketenagakerjaan`, `bekerja`, `angkatan kerja`; `tpt` -> `tingkat pengangguran terbuka`.
3. Bot mencari di indeks lokal SQLite lebih dulu, lalu mencoba memperbarui hasil dari BPS WebAPI.
4. Bot menembak BPS WebAPI dengan beberapa keyword kandidat ke tiga sumber: tabel dinamis, SIMDASI, dan publikasi.
5. Hasil pencarian ditampilkan per kelompok sumber dengan urutan tabel dinamis, SIMDASI, lalu publikasi. Kalau salah satu sumber error, sumber itu disembunyikan dan sumber lain yang berhasil tetap ditampilkan.
6. Bot juga memakai index/cache variabel BPS dari halaman `model/var` agar pencarian berikutnya lebih cepat dan tetap punya fallback saat API sedang bermasalah.
7. Hasil diranking memakai exact match, acronym match, sinonim, fuzzy similarity, dan konteks domain.
8. Daftar pilihan menampilkan label sumber di judul, misalnya `[Tabel Dinamis] Jumlah Penduduk`, supaya pengguna tahu data berasal dari tabel dinamis, SIMDASI, atau publikasi.
9. Pilihan user disimpan di sesi. Kalau user pernah memilih salah satu variabel untuk keyword tertentu, pilihan itu akan naik prioritas pada pencarian mirip berikutnya.
10. Jika tabel dinamis tidak ditemukan, bot tetap menampilkan kemungkinan dari SIMDASI dan publikasi bila ada.

Format fallback:

- SIMDASI: bot mencoba membaca endpoint `view` tabel statis. Jika isi tabel tersedia sebagai matrix/HTML/rows, bot mengirimkannya sebagai tabel teks seperti tabel dinamis. Jika isi tabel tidak bisa dibaca otomatis, bot mengirim metadata tabel SIMDASI.
- Publikasi: setelah pengguna memilih salah satu publikasi, bot langsung mengirim detail publikasi tanpa bertanya tahun lagi karena tahun publikasi sudah melekat pada judul dan tanggal rilis. Bot mengirim judul, tanggal rilis, ringkasan lengkap jika ada, dan link halaman detail publikasi BPS. Link PDF/download mentah dari WebAPI tidak dikirim ke pengguna.

Balasan bot tidak dipotong oleh formatter aplikasi. Jika teks terlalu panjang untuk satu pesan WhatsApp, `GowaClient` membaginya menjadi beberapa pesan berurutan tanpa menghapus isi.

Judul hasil data juga diberi label sumber:

- `[Tabel Dinamis] ...` untuk tabel dinamis BPS WebAPI.
- `[SIMDASI] ...` untuk tabel statis/SIMDASI.
- `[Publikasi] ...` untuk publikasi BPS.

URL contoh pencarian variabel:

```text
https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/1/keyword/ipm/area/1/key/API_KEY
```

Untuk keyword dengan spasi seperti `indeks pembangunan`:

```text
https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/1/keyword/indeks%20pembangunan/area/1/key/API_KEY
```

`area/1` dipakai pada beberapa endpoint BPS untuk membatasi hasil area/domain. Jika endpoint BPS error 403 atau 500 dengan `area/1`, bot otomatis mencoba ulang tanpa `area`.

## Mengelola Index Lokal BPS

Index lokal tersimpan di SQLite yang sama dengan cache:

```text
/app/data/bps_cache.sqlite3
```

Di Docker, file itu berada di volume `marawa-data`. Tabel index bernama `bps_index` dan dipakai sebagai fallback saat BPS WebAPI sedang error. Bot tetap mencoba WebAPI live, tetapi jika salah satu sumber gagal, sumber itu disembunyikan dan hasil dari index/sumber lain tetap dipakai.

Isi `bps_index`:

| Kolom | Isi |
| --- | --- |
| `namespace` | Namespace cache untuk base URL dan API key saat ini |
| `domain` | Domain BPS, misalnya `1306` |
| `source_type` | `dynamic_table`, `simdasi`, atau `publication` |
| `item_key` | ID unik item, misalnya `dynamic_table:123` |
| `title_norm` | Judul yang sudah dinormalisasi untuk pencarian |
| `payload` | JSON item data |
| `updated_at` | Timestamp update |

### Menambah Index Dari WebAPI

Cara paling aman adalah menjalankan pencarian agar bot menarik data dari WebAPI lalu menyimpan hasilnya ke index:

```powershell
@'
import asyncio
from app.config import get_settings
from app.services.bps_client import BpsClient

async def main():
    s = get_settings()
    client = BpsClient(
        s.bps_api_key,
        s.bps_domain,
        cache_ttl_seconds=s.bps_cache_ttl_seconds,
        cache_db_path=s.bps_cache_db_path,
    )
    for query in ["stunting", "pertanian", "luas panen", "produksi padi"]:
        result = await client.search_variable_options(query)
        print(query, result.found, {
            key: len(value)
            for key, value in (result.metadata or {}).get("source_groups", {}).items()
        })

asyncio.run(main())
'@ | docker compose exec -T marawa-bot python -
```

Jika WebAPI berhasil membalas, hasil dari `dynamic_table`, `simdasi`, dan `publication` otomatis masuk ke `bps_index`.

### Menambah Keyword Warmup Startup

Untuk keyword yang sering dipakai, tambahkan ke `BPS_INDEX_WARMUP_QUERIES` di [app/main.py](D:/Code/bps-marawa/app/main.py). Saat bot startup, keyword itu dicari otomatis dan hasilnya disimpan ke index lokal.

Contoh keyword yang sudah di-warmup:

```python
BPS_INDEX_WARMUP_QUERIES = ("penduduk", "jumlah penduduk", "ipm", "pdrb", "kemiskinan", "tpt", "tpak")
```

Setelah mengubah daftar warmup, rebuild container:

```powershell
docker compose up -d --build marawa-bot
```

### Melihat Isi Index

```powershell
@'
import sqlite3
from app.config import get_settings

s = get_settings()
connection = sqlite3.connect(s.bps_cache_db_path)
for row in connection.execute(
    "SELECT source_type, COUNT(*) FROM bps_index GROUP BY source_type ORDER BY source_type"
):
    print(row)
'@ | docker compose exec -T marawa-bot python -
```

Untuk melihat beberapa judul:

```powershell
@'
import json
import sqlite3
from app.config import get_settings

s = get_settings()
connection = sqlite3.connect(s.bps_cache_db_path)
rows = connection.execute(
    "SELECT source_type, payload FROM bps_index ORDER BY updated_at DESC LIMIT 20"
).fetchall()
for source_type, payload_json in rows:
    payload = json.loads(payload_json)
    print(source_type, "-", payload.get("title") or payload.get("judul"))
'@ | docker compose exec -T marawa-bot python -
```

### Edit Manual

Edit manual bisa dilakukan langsung ke tabel `bps_index`, tetapi lebih berisiko karena `payload` harus JSON yang sesuai dengan format sumbernya. Untuk `dynamic_table`, minimal payload perlu berisi:

```json
{
  "source_type": "dynamic_table",
  "source_label": "Tabel Dinamis",
  "var_id": 123,
  "title": "Jumlah Penduduk",
  "unit": "jiwa"
}
```

Untuk saat ini, cara yang direkomendasikan tetap lewat script pencarian di atas atau menambahkan keyword ke warmup. Kalau perlu edit manual rutin, buat script khusus agar format JSON dan `item_key` tidak salah.

## Admin Handoff

User masuk mode admin dengan memilih menu 3 atau mengetik `admin`.

Flow lengkap:

1. User minta bicara admin → state `WAITING_ADMIN`, notifikasi ke semua `ADMIN_NUMBERS`.
2. Admin ambil alih dengan mengirim ke nomor bot: `ambil 628xxxxxxxx` → state `TALKING_TO_ADMIN`, user dapat notifikasi "Admin sudah terhubung".
3. Admin berkomunikasi dengan user melalui WA bot. Bot diam total (semua pesan user diabaikan, termasuk `batal`/`menu`).
4. Admin selesai, kirim ke nomor bot: `selesai 628xxxxxxxx` → bot aktif kembali, user dapat menu utama.

Timeout:
- Jika admin belum `ambil` dalam `ADMIN_PICKUP_TIMEOUT_SECONDS` (default 5 menit), bot kembali aktif + info "admin belum merespons".
- Jika user diam selama 5 menit setelah admin terhubung (`TALKING_TO_ADMIN`), percakapan diakhiri otomatis.
- Jika admin lupa `selesai` dan tidak ada aktivitas selama 30 menit, bot otomatis aktif kembali.

Command admin (dikirim dari nomor admin ke nomor bot):
- `ambil 628xxx` — mengambil alih percakapan user
- `selesai 628xxx` — mengakhiri sesi admin, bot aktif kembali

## AI Provider

Saat ini konfigurasi utama:

- `AI_PROVIDER=openai`
- `AI_PROVIDER=ollama`

Model lain seperti Gemini, DeepSeek, Kimi via OpenRouter, atau provider OpenAI-compatible bisa ditambahkan lewat adapter baru di [app/services/ai_client.py](D:/Code/bps-marawa/app/services/ai_client.py). Prinsipnya, AI hanya mengembalikan JSON intent/query/tahun/triwulan, lalu aplikasi yang memvalidasi dan menjalankan aksi.

Guardrail penting:

- User text dianggap data tidak tepercaya.
- Prompt injection dan permintaan secret/tool internal ditolak.
- Aksi agent dibatasi di [app/services/guarded_agent.py](D:/Code/bps-marawa/app/services/guarded_agent.py).

## Kalimat Yang Bisa Diatur Sendiri

Ubah kalimat di file berikut:

| Kebutuhan | Lokasi |
| --- | --- |
| Salam sesi baru dan daftar layanan | [app/conversation/engine.py](D:/Code/bps-marawa/app/conversation/engine.py), fungsi `intro_message()` |
| Menu utama | [app/conversation/engine.py](D:/Code/bps-marawa/app/conversation/engine.py), fungsi `main_menu()` |
| Pertanyaan "Data apa yang dicari?" | [app/conversation/engine.py](D:/Code/bps-marawa/app/conversation/engine.py), bagian `Intent.DATA_REQUEST` |
| Pilihan hasil data mirip | [app/conversation/engine.py](D:/Code/bps-marawa/app/conversation/engine.py), fungsi `_format_data_options()` |
| Footer data tidak ditemukan | [app/conversation/engine.py](D:/Code/bps-marawa/app/conversation/engine.py), fungsi `_with_data_help_footer()` |
| Link konsultasi | [app/conversation/engine.py](D:/Code/bps-marawa/app/conversation/engine.py), konstanta `CONSULTATION_LINK` |
| Pesan admin aktif/nonaktif | [app/conversation/engine.py](D:/Code/bps-marawa/app/conversation/engine.py), fungsi `admin_finished_user_message()` dan bagian `Intent.ADMIN` |
| Notifikasi ke admin | [app/services/admin_handoff.py](D:/Code/bps-marawa/app/services/admin_handoff.py), fungsi `_summary()` |
| Format tabel data | [app/services/bps_client.py](D:/Code/bps-marawa/app/services/bps_client.py), fungsi `_format_table_message()` |
| Keyword tambahan topik luas | [app/services/bps_client.py](D:/Code/bps-marawa/app/services/bps_client.py), konstanta `TOPIC_KEYWORD_EXPANSIONS` |
| Aturan prompt injection | [app/services/ai_client.py](D:/Code/bps-marawa/app/services/ai_client.py), konstanta `INJECTION_PATTERNS` |
| Aksi AI yang boleh dilakukan | [app/services/guarded_agent.py](D:/Code/bps-marawa/app/services/guarded_agent.py), konstanta `ALLOWED_AGENT_ACTIONS` |

Setelah mengubah kalimat atau kode, restart bot:

```powershell
docker compose up -d --force-recreate marawa-bot
```

## Proteksi Pesan Duplikat dan Stale

Bot punya beberapa lapisan proteksi agar tidak memproses pesan yang seharusnya diabaikan:

1. **Outgoing message filter**: pesan dari bot sendiri (flag `fromMe`) langsung di-skip.
2. **Bot own number filter**: jika `BOT_PHONE_NUMBER` diset di `.env`, pesan dari nomor tersebut langsung di-skip.
3. **Stale message filter**: pesan yang timestamp-nya lebih dari 2 menit (dari payload webhook) langsung di-skip. Ini mencegah GOWA replay pesan lama saat server restart.
4. **Status update filter**: delivery receipt, read receipt, dan status broadcast diabaikan.
5. **Duplicate inbound filter**: pesan dengan ID atau konten yang sama dalam 60 detik terakhir di-skip.
6. **Bot echo filter**: jika teks pesan masuk sama persis dengan pesan yang baru saja dikirim bot (dalam 2 menit terakhir), di-skip. Bot menyimpan sampai 20 pesan terakhir per nomor.

Semua filter ini mencegah masalah "bot tiba-tiba kirim pesan saat server baru dinyalakan".

## Timeout

Timer idle dihitung dari **pesan terakhir yang bot kirim** ke user. Timer di-reset setiap kali user mengirim pesan.

- Session user umum: `SESSION_TIMEOUT_SECONDS=600` (10 menit sejak bot terakhir kirim pesan).
- Tunggu admin pickup: `ADMIN_PICKUP_TIMEOUT_SECONDS=300` (5 menit).
- Admin talk idle: jika user diam 5 menit setelah admin terhubung, percakapan diakhiri.
- Admin handoff stuck: jika admin tidak merespons dalam 30 menit, bot otomatis aktif kembali.

Nilai bisa diubah di `.env`.

## Cache BPS

Bot memakai cache hybrid:

- Memory cache untuk akses cepat selama container berjalan.
- SQLite cache di `BPS_CACHE_DB_PATH` agar hasil request BPS tetap tersimpan setelah container restart.

Default:

```env
BPS_CACHE_TTL_SECONDS=3600
BPS_CACHE_DB_PATH=/app/data/bps_cache.sqlite3
```

File SQLite disimpan di Docker volume `marawa-data`. Untuk mematikan cache, set:

```env
BPS_CACHE_TTL_SECONDS=0
```

## Test

Lewat Docker:

```powershell
docker compose build marawa-bot
docker compose run --rm marawa-bot sh -lc "pip install -r requirements-dev.txt && python -m pytest"
```

Kalau Python lokal tersedia:

```powershell
pip install -r requirements-dev.txt
pytest
```

## BPS Proxy (Home Server) — Penjelasan Lengkap

### Kenapa Perlu Proxy?

BPS WebAPI (`webapi.bps.go.id`) **memblokir IP dari cloud/VPS**. Artinya kalau bot lo jalan di DigitalOcean, AWS, GCP, atau VPS manapun — semua request ke BPS pasti kena **403 Forbidden**.

Yang bisa akses BPS WebAPI hanya:
- IP rumah (internet Indihome, MyRepublic, dsb)
- IP kantor

Bot lo kan jalan di cloud server. Jadi bot **tidak bisa langsung** minta data ke BPS.

Selain itu, BPS WebAPI pakai **Cloudflare Bot Management** yang minta JavaScript challenge — tidak bisa di-bypass dengan HTTP client biasa. Proxy ini pakai **Playwright (Chromium headless)** untuk solve challenge tersebut.

### Cara Kerja Proxy

```
┌─────────────────────────────────────────────────────────────────────┐
│                         ALUR REQUEST                                 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  [User WA] → [GOWA] → [Bot di Cloud Server]                        │
│                              │                                      │
│                              │ bot butuh data BPS                    │
│                              ▼                                      │
│                     [Tunnel / Internet]                              │
│                              │                                      │
│                              ▼                                      │
│              [Home Server - Proxy (Playwright)]                      │
│                              │                                      │
│              ┌───────────────┴────────────────┐                     │
│              │ Request pertama:                │ Request berikutnya: │
│              │ Chromium solve CF challenge     │ httpx + cookies     │
│              │ → dapat cookie cf_clearance     │ (jauh lebih cepat)  │
│              └───────────────┬────────────────┘                     │
│                              │ forward request + cookie              │
│                              ▼                                      │
│                    [webapi.bps.go.id]                                │
│                              │                                      │
│                              │ BPS kasih response (JSON)             │
│                              ▼                                      │
│              [Home Server - Proxy]  ──→  [Tunnel]  ──→  [Bot]      │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**Penjelasan alur:**

1. **Request pertama** (atau saat cookie expired): Proxy buka Chromium headless, buka URL BPS, tunggu Cloudflare challenge selesai otomatis, ambil cookie `cf_clearance` yang didapat.
2. **Request berikutnya**: Proxy pakai `httpx` biasa + cookie tadi → jauh lebih cepat, tidak perlu buka browser lagi.
3. **Cookie refresh**: Cookie berlaku ~30 menit. Proxy auto-refresh setiap 25 menit atau kalau tiba-tiba kena challenge lagi.

### Apa Itu Tunnel?

Home server lo di belakang NAT (router rumah) — dari internet tidak bisa langsung akses. Tunnel = cara expose home server ke internet tanpa buka port router.

| Opsi | Cara Kerja | Perlu Domain? | Gratis? |
|------|-----------|---------------|---------|
| **Cloudflare Tunnel** | Cloudflare jadi perantara, dapet URL publik | Ya (domain di Cloudflare) | Ya |
| **Tailscale** | VPN mesh, cloud server & home server satu jaringan | Tidak | Ya (gratis 100 device) |
| **ZeroTier** | Mirip Tailscale, VPN mesh | Tidak | Ya (gratis 25 device) |

---

### STEP 1: Setup Proxy di Home Server

#### Prasyarat

- PC/laptop/Raspberry Pi yang **selalu nyala** dan **terkoneksi internet rumah**
- Docker terinstall
- RAM minimal **1GB** (Chromium headless butuh memory)

#### 1.1. Copy file proxy ke home server

```bash
# Opsi A: clone repo di home server
git clone https://github.com/alfathhh/marawav999.git
cd marawav999/proxy

# Opsi B: copy via SCP dari laptop
scp -r proxy/ user@ip-home-server:~/bps-proxy/
cd ~/bps-proxy
```

#### 1.2. Build dan jalankan proxy

```bash
docker compose up -d --build
```

> **Note:** Build pertama agak lama (~5-10 menit) karena Playwright perlu download Chromium (~150MB).

#### 1.3. Cek proxy sudah jalan

```bash
docker compose logs -f bps-proxy
```

Tunggu sampai muncul log:
```
Playwright browser ready
INFO:     Application startup complete.
```

#### 1.4. Test proxy

```bash
curl "http://localhost:8001/health"
# Harus balas: {"status":"ok","browser":"playwright"}
```

Lalu test akses BPS (request pertama akan lambat ~5-10 detik karena Chromium solve challenge):

```bash
curl "http://localhost:8001/v1/api/list/model/var/lang/ind/domain/1306/page/1/key/API_KEY_LO"
# Harus balas JSON data dari BPS
```

Request berikutnya akan jauh lebih cepat karena pakai cookie cache.

#### 1.5. Troubleshooting build

Kalau build gagal karena missing apt dependencies:

```bash
docker compose down
docker compose up -d --build --no-cache
```

Kalau container crash dengan error `libgobject` atau sejenisnya, coba update base image:

```bash
# Edit Dockerfile, ganti FROM python:3.12-slim dengan:
FROM python:3.12-bullseye
```

---

### STEP 2: Setup Tunnel (Pilih Salah Satu)

Setelah proxy jalan di home server, lo perlu bikin supaya bot di cloud server bisa "nyambung" ke proxy di home server.

---

#### OPSI A: Cloudflare Tunnel (Recommended)

**Kelebihan:** Paling stabil, auto-SSL, bisa custom domain.
**Syarat:** Lo punya domain yang DNS-nya di Cloudflare.

##### A.1. Install `cloudflared` di home server

```bash
# Debian/Ubuntu
curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb

# Atau pakai Docker (alternatif)
docker pull cloudflare/cloudflared:latest
```

##### A.2. Login ke Cloudflare

```bash
cloudflared tunnel login
```

Ini buka browser → login Cloudflare → pilih domain → selesai. File credential tersimpan di `~/.cloudflared/`.

##### A.3. Buat tunnel

```bash
cloudflared tunnel create bps-proxy
```

Output: `Created tunnel bps-proxy with id xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`

Catat **TUNNEL_ID** ini.

##### A.4. Buat config

Buat file `~/.cloudflared/config.yml`:

```yaml
tunnel: bps-proxy
credentials-file: /home/USERNAME/.cloudflared/TUNNEL_ID.json

ingress:
  - hostname: bps-proxy.domainlo.com
    service: http://localhost:8001
  - service: http_status:404
```

Ganti:
- `USERNAME` = username linux lo di home server
- `TUNNEL_ID` = ID dari step A.3
- `bps-proxy.domainlo.com` = subdomain yang lo mau pakai

##### A.5. Tambah DNS record

```bash
cloudflared tunnel route dns bps-proxy bps-proxy.domainlo.com
```

Ini otomatis bikin CNAME record di Cloudflare DNS.

##### A.6. Jalankan tunnel

Test dulu:
```bash
cloudflared tunnel run bps-proxy
```

Kalau udah oke, jadikan service (auto-start saat boot):
```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

##### A.7. Test dari mana saja

```bash
curl https://bps-proxy.domainlo.com/health
# Harus balas: {"status":"ok"}
```

Kalau berhasil, berarti tunnel nyambung. Bot di cloud server bisa pakai URL ini.

**Untuk bot, set di `.env`:**
```env
BPS_BASE_URL=https://bps-proxy.domainlo.com/v1/api
```

---

#### OPSI B: Tailscale

**Kelebihan:** Paling gampang, zero config, ga perlu domain.
**Cara kerja:** Bikin VPN antara cloud server dan home server. Keduanya bisa saling akses via IP private Tailscale (100.x.x.x).

##### B.1. Install Tailscale di KEDUA server (home + cloud)

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Pertama kali jalankan → buka URL yang muncul → login/approve device.

##### B.2. Cek IP Tailscale home server

```bash
# Di home server:
tailscale ip -4
# Output contoh: 100.64.0.5
```

Catat IP ini.

##### B.3. Test dari cloud server

```bash
# Di cloud server (yang jalan bot):
curl http://100.64.0.5:8001/health
# Harus balas: {"status":"ok"}
```

##### B.4. Selesai

**Untuk bot, set di `.env`:**
```env
BPS_BASE_URL=http://100.64.0.5:8001/v1/api
```

> Note: Tailscale pakai HTTP (bukan HTTPS) karena traffic sudah encrypted oleh WireGuard tunnel-nya Tailscale.

---

#### OPSI C: ZeroTier

**Kelebihan:** Mirip Tailscale, open-source.
**Cara kerja:** Sama kayak Tailscale — VPN mesh, keduanya dapat IP virtual (misal 10.x.x.x).

##### C.1. Buat network di ZeroTier Central

Buka https://my.zerotier.com → Create Network → catat **Network ID**.

##### C.2. Install ZeroTier di KEDUA server

```bash
curl -s https://install.zerotier.com | sudo bash
sudo zerotier-cli join NETWORK_ID_LO
```

##### C.3. Approve device di ZeroTier Central

Buka https://my.zerotier.com → network lo → centang kedua device agar "Authorized".

##### C.4. Cek IP ZeroTier home server

```bash
# Di home server:
sudo zerotier-cli listnetworks
# Cari IP yang dikasih, misal 10.147.20.5
```

##### C.5. Test dari cloud server

```bash
curl http://10.147.20.5:8001/health
```

##### C.6. Selesai

**Untuk bot, set di `.env`:**
```env
BPS_BASE_URL=http://10.147.20.5:8001/v1/api
```

---

### STEP 3: Konfigurasi Bot

Setelah tunnel aktif dan lo bisa akses proxy dari cloud server, tinggal update config bot.

#### 3.1. Edit `.env` di cloud server (tempat bot jalan)

```env
# Sebelumnya (langsung ke BPS - kena blokir):
# BPS_BASE_URL=https://webapi.bps.go.id/v1/api

# Sekarang (lewat proxy):
BPS_BASE_URL=https://bps-proxy.domainlo.com/v1/api    # Cloudflare Tunnel
# atau
BPS_BASE_URL=http://100.64.0.5:8001/v1/api            # Tailscale
# atau
BPS_BASE_URL=http://10.147.20.5:8001/v1/api           # ZeroTier
```

#### 3.2. Restart bot

```bash
docker compose up -d --force-recreate marawa-bot
```

#### 3.3. Verifikasi bot bisa ambil data

```bash
docker compose logs -f marawa-bot
```

Cari log `bps.index.warmup` — kalau ada `found=True` atau jumlah items > 0, berarti bot berhasil ambil data BPS lewat proxy.

---

### Troubleshooting

| Masalah | Solusi |
|---------|--------|
| Proxy health OK tapi BPS request gagal | Cek API key BPS masih valid |
| Tunnel tidak bisa diakses dari cloud | Cek `cloudflared`/`tailscale`/`zerotier` masih jalan di home server |
| Bot log: connection refused | Cek URL di `BPS_BASE_URL` benar, proxy container jalan |
| Bot log: timeout | Home server internet lambat, atau proxy container mati |
| Cloudflare: 502 Bad Gateway | Proxy container di home server mati. Cek `docker compose ps` |

### Monitoring Proxy

Cek status proxy di home server:

```bash
# Cek container jalan
docker compose ps

# Cek log proxy
docker compose logs -f bps-proxy

# Restart kalau bermasalah
docker compose restart bps-proxy
```

## Referensi

- [GOWA GitHub](https://github.com/aldinokemal/go-whatsapp-web-multidevice)
- [GOWA webhook payload](https://github.com/aldinokemal/go-whatsapp-web-multidevice/blob/main/docs/webhook-payload.md)
- [BPS WebAPI](https://webapi.bps.go.id/)
