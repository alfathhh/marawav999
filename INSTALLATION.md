# Panduan Instalasi Marawa BPS Padang Pariaman

Dokumen ini menjelaskan cara memasang, menjalankan, menguji, dan mengubah kalimat/menu chatbot Marawa dari awal sampai siap dipakai.

## 1. Prasyarat

Pastikan perangkat/server sudah punya:

- Docker Desktop atau Docker Engine.
- Docker Compose.
- Git, jika proyek diambil dari repository.
- Koneksi internet untuk menarik image Docker dan dependency Python.
- BPS WebAPI key.
- Nomor WhatsApp yang akan dipakai sebagai nomor bot.
- Akun Google Cloud service account jika ingin logging ke Google Sheets.

Python lokal tidak wajib, karena bot dijalankan melalui Docker.

## 2. Struktur Proyek

Folder utama:

```text
D:\Code\bps-marawa
+-- app/
|   +-- main.py
|   +-- config.py
|   +-- conversation/
|   |   +-- engine.py
|   |   +-- intent_parser.py
|   |   +-- session_store.py
|   +-- services/
|       +-- ai_client.py
|       +-- bps_client.py
|       +-- gowa_client.py
|       +-- google_sheets_logger.py
|       +-- admin_handoff.py
+-- tests/
+-- docker-compose.yml
+-- Dockerfile
+-- .env.example
+-- requirements.txt
+-- requirements-dev.txt
```

File penting:

- `app/main.py`: endpoint HTTP, webhook GOWA, health check.
- `app/conversation/engine.py`: alur percakapan, menu, kalimat bot, handoff admin.
- `app/conversation/intent_parser.py`: kata kunci dan pemetaan intent sederhana.
- `app/services/bps_client.py`: pencarian data BPS WebAPI.
- `app/services/gowa_client.py`: pengiriman pesan WhatsApp lewat GOWA.
- `.env`: konfigurasi rahasia dan environment runtime.

## 3. Siapkan Konfigurasi `.env`

Salin file contoh:

```powershell
Copy-Item .env.example .env
```

Isi `.env`:

```env
BPS_API_KEY=isi-dengan-api-key-bps
BPS_DOMAIN=1306

AI_PROVIDER=openai
OPENAI_API_KEY=isi-dengan-openai-api-key
OPENAI_MODEL=gpt-4o-mini
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_MODEL=llama3.1

GOWA_BASE_URL=http://gowa:3000
GOWA_BASIC_AUTH_USER=marawa
GOWA_BASIC_AUTH_PASS=password-yang-kuat
GOWA_WEBHOOK_SECRET=secret-yang-panjang-dan-acak

ADMIN_NUMBERS=628xxxx,628yyyy
GOOGLE_SHEETS_SPREADSHEET_ID=isi-jika-pakai-google-sheets
GOOGLE_SERVICE_ACCOUNT_JSON=isi-json-service-account-dalam-satu-baris

SESSION_TIMEOUT_SECONDS=600
ADMIN_PICKUP_TIMEOUT_SECONDS=300
```

Catatan:

- `BPS_DOMAIN=1306` adalah domain BPS Kabupaten Padang Pariaman.
- `ADMIN_NUMBERS` dipisahkan koma tanpa spasi wajib.
- Nomor WhatsApp memakai format internasional tanpa tanda `+`, contoh `6281234567890`.
- Jika Google Sheets belum siap, kosongkan `GOOGLE_SHEETS_SPREADSHEET_ID` dan `GOOGLE_SERVICE_ACCOUNT_JSON`; bot tetap berjalan.

## 4. Menjalankan Bot

Build image bot:

```powershell
docker compose build marawa-bot
```

Jalankan semua service:

```powershell
docker compose up -d
```

Cek container:

```powershell
docker compose ps
```

Cek log bot:

```powershell
docker compose logs -f marawa-bot
```

Cek log GOWA:

```powershell
docker compose logs -f gowa
```

## 5. Scan QR WhatsApp GOWA

Buka halaman GOWA:

```text
http://localhost:3000/app/login
```

Login basic auth memakai:

- Username: nilai `GOWA_BASIC_AUTH_USER`
- Password: nilai `GOWA_BASIC_AUTH_PASS`

Scan QR menggunakan WhatsApp nomor bot.

Setelah tersambung, GOWA akan mengirim event pesan ke:

```text
http://marawa-bot:8000/webhook/gowa
```

## 6. Cek Health Check

Jalankan:

```powershell
curl http://localhost:8000/health
```

Respons normal:

```json
{"status":"ok"}
```

## 7. Menjalankan Test

Jalankan test di Docker:

```powershell
docker compose run --rm marawa-bot sh -lc "pip install -r requirements-dev.txt && python -m pytest"
```

Hasil normal:

```text
11 passed
```

## 8. Alur Percakapan Default

Menu utama:

```text
1. Mencari data statistik BPS Kabupaten Padang Pariaman
2. Rekomendasi dan konsultasi statistik
3. Menghubungkan Anda dengan admin
4. Mengakhiri percakapan
```

User boleh mengetik:

- angka, contoh `1`
- kata kunci, contoh `data penduduk`
- kalimat natural, contoh `saya mau cari data PDRB Padang Pariaman terbaru`

Intent yang dikenali:

- permintaan data
- rekomendasi/konsultasi statistik
- bicara admin
- keluar
- ambigu

## 9. Cara Kerja Pencarian Data BPS

File utama:

```text
app/services/bps_client.py
```

Alur pencarian data:

1. Bot menerima pertanyaan user.
2. AI/parser mengekstrak keyword, contoh `penduduk`.
3. `BpsClient` mencari variabel BPS dengan `model=var`.
4. Bot resolve parameter tabel dinamis:
   - `th`: tahun terbaru
   - `turth`: tahunan/total
   - `vervar`: Padang Pariaman
   - `turvar`: total
5. Bot mengambil data dengan `model=data`.
6. Jika data tabel dinamis kosong, bot fallback ke `model=publication`.
7. Bot menjawab dengan ringkasan, tahun, satuan, sumber, dan URL sumber jika tersedia.

## 10. Cara Mengubah Menu Utama

Menu utama ada di:

```text
app/conversation/engine.py
```

Cari function:

```python
def main_menu(prefix: str = "Halo, saya Marawa BPS Padang Pariaman.") -> str:
    return (
        f"{prefix}\n\n"
        "1. Mencari data statistik BPS Kabupaten Padang Pariaman\n"
        "2. Rekomendasi dan konsultasi statistik\n"
        "3. Menghubungkan Anda dengan admin\n"
        "4. Mengakhiri percakapan"
    )
```

Contoh mengganti teks menu:

```python
def main_menu(prefix: str = "Halo, saya Marawa, asisten data BPS Padang Pariaman.") -> str:
    return (
        f"{prefix}\n\n"
        "1. Cari data statistik\n"
        "2. Konsultasi statistik\n"
        "3. Hubungi admin\n"
        "4. Selesai"
    )
```

Setelah mengubah menu, jalankan test:

```powershell
docker compose run --rm marawa-bot sh -lc "pip install -r requirements-dev.txt && python -m pytest"
```

Lalu rebuild dan restart:

```powershell
docker compose build marawa-bot
docker compose up -d
```

## 11. Cara Mengubah Kalimat Bot

Sebagian besar kalimat balasan ada di:

```text
app/conversation/engine.py
```

Contoh kalimat tanya data:

```python
return BotResponse("Data apa yang dicari?", intent)
```

Bisa diganti menjadi:

```python
return BotResponse("Boleh tuliskan data statistik yang ingin dicari?", intent)
```

Contoh kalimat konsultasi:

```python
f"Untuk rekomendasi dan konsultasi statistik, silakan isi buku tamu PST: {CONSULTATION_LINK}\n\n"
"Jika ingin dibantu petugas, ketik admin."
```

Bisa diganti menjadi:

```python
f"Untuk layanan konsultasi statistik, silakan isi buku tamu PST: {CONSULTATION_LINK}\n\n"
"Jika ingin berbicara dengan petugas, ketik admin."
```

Contoh kalimat data tidak ditemukan:

```python
f"Maaf, data tersebut belum ditemukan. Silakan ajukan konsultasi melalui {CONSULTATION_LINK}."
```

Bisa diganti menjadi:

```python
f"Maaf, saya belum menemukan data tersebut di WebAPI BPS. Untuk bantuan lanjutan, silakan isi {CONSULTATION_LINK}."
```

## 12. Cara Mengubah Kata Kunci Intent

File:

```text
app/conversation/intent_parser.py
```

Contoh:

```python
DATA_WORDS = ("data", "jumlah", "berapa", "penduduk", "kemiskinan", "pdrb", "inflasi", "ipm", "pengangguran")
CONSULT_WORDS = ("konsultasi", "rekomendasi", "pst", "tamu", "layanan")
ADMIN_WORDS = ("admin", "operator", "petugas", "pegawai", "manusia", "cs", "customer service")
EXIT_WORDS = ("keluar", "selesai", "stop", "akhiri")
```

Jika ingin kata `survei` dianggap permintaan data:

```python
DATA_WORDS = ("data", "jumlah", "berapa", "penduduk", "kemiskinan", "pdrb", "inflasi", "ipm", "pengangguran", "survei")
```

Jika ingin kata `helpdesk` dianggap admin:

```python
ADMIN_WORDS = ("admin", "operator", "petugas", "pegawai", "manusia", "cs", "customer service", "helpdesk")
```

Setelah mengubah intent parser, update test jika perlu di:

```text
tests/test_intent_parser.py
```

## 13. Cara Mengubah Link Konsultasi

File:

```text
app/conversation/engine.py
```

Cari:

```python
CONSULTATION_LINK = "https://s.bps.go.id/tamu1306"
```

Ganti sesuai kebutuhan:

```python
CONSULTATION_LINK = "https://link-baru"
```

## 14. Cara Mengubah Nomor Admin

Tidak perlu edit kode.

Ubah `.env`:

```env
ADMIN_NUMBERS=6281111111111,6282222222222
```

Restart service:

```powershell
docker compose up -d --force-recreate marawa-bot
```

## 15. Cara Mengubah Timeout

Ubah `.env`:

```env
SESSION_TIMEOUT_SECONDS=600
ADMIN_PICKUP_TIMEOUT_SECONDS=300
```

Arti:

- `SESSION_TIMEOUT_SECONDS=600`: sesi user timeout setelah 10 menit.
- `ADMIN_PICKUP_TIMEOUT_SECONDS=300`: admin dianggap sibuk jika belum pickup setelah 5 menit.

Restart:

```powershell
docker compose up -d --force-recreate marawa-bot
```

## 16. Cara Update Kode Setelah Edit

Setelah mengubah file Python:

```powershell
docker compose build marawa-bot
docker compose up -d
```

Cek log:

```powershell
docker compose logs -f marawa-bot
```

Jalankan test:

```powershell
docker compose run --rm marawa-bot sh -lc "pip install -r requirements-dev.txt && python -m pytest"
```

## 17. Troubleshooting

### Bot tidak membalas pesan

Cek:

```powershell
docker compose ps
docker compose logs -f marawa-bot
docker compose logs -f gowa
```

Pastikan:

- GOWA sudah login WhatsApp.
- `.env` punya `GOWA_WEBHOOK_SECRET` yang sama dengan config GOWA.
- Event webhook GOWA adalah `message`.
- Bot bisa mengakses GOWA dengan `GOWA_BASE_URL=http://gowa:3000`.

### Health check gagal

Jalankan:

```powershell
docker compose up -d --build
docker compose logs -f marawa-bot
```

### Data BPS tidak ditemukan

Cek:

- `BPS_API_KEY` sudah benar.
- `BPS_DOMAIN=1306`.
- Pertanyaan user cukup spesifik.
- Data memang tersedia di WebAPI BPS.

Jika tabel dinamis gagal, bot otomatis mencari publikasi.

### Google Sheets tidak terisi

Cek:

- `GOOGLE_SHEETS_SPREADSHEET_ID` sudah benar.
- `GOOGLE_SERVICE_ACCOUNT_JSON` valid dan dalam satu baris.
- Service account sudah diberi akses edit ke spreadsheet.
- Nama sheet adalah `users` dan `conversations`.

### Docker build putus atau lambat

Coba ulang:

```powershell
docker compose build marawa-bot
```

Jika masih gagal, restart Docker Desktop lalu ulangi.

## 18. Checklist Deploy

- `.env` sudah diisi.
- `docker compose build marawa-bot` sukses.
- `docker compose up -d` sukses.
- `GET /health` mengembalikan `{"status":"ok"}`.
- GOWA sudah scan QR dan connected.
- Nomor admin sudah benar.
- Test percakapan:
  - ketik `menu`
  - pilih `1`
  - cari data, contoh `jumlah penduduk terbaru`
  - pilih `2`
  - pilih `3`
  - admin balas `selesai <nomor_user>`
  - ketik `keluar`
