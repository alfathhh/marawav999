# Marawa BPS Padang Pariaman

Chatbot WhatsApp Python untuk layanan data BPS Kabupaten Padang Pariaman. Bot menerima webhook dari GOWA, menjaga sesi percakapan, mencari data di BPS WebAPI domain `1306`, memakai AI sebagai parser aman, melakukan handoff admin, dan mencatat log ke Google Spreadsheet.

## Arsitektur

- `gowa`: WhatsApp gateway memakai image `aldinokemal2104/go-whatsapp-web-multidevice`.
- `marawa-bot`: FastAPI webhook receiver di `POST /webhook/gowa`.
- `app/conversation`: state machine, session store, timeout, parsing tahun/triwulan.
- `app/services`: adapter GOWA, BPS WebAPI, AI, Google Sheets, admin handoff.
- `GuardedDataAgent`: AI hanya boleh memilih aksi yang diizinkan aplikasi, bukan menjalankan tool bebas.

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

1. Mencari data statistik BPS Kabupaten Padang Pariaman.
2. Rekomendasi dan konsultasi statistik.
3. Menghubungkan Anda dengan admin.
4. Mengakhiri percakapan.

Pada sesi baru, pesan pertama user selalu dibalas greeting/menu saja. Pesan pertama belum diproses sebagai permintaan data, admin, atau menu. Ini membuat percakapan aman setelah server/container restart karena semua session in-memory akan reset dan user selalu mulai dari awal lagi. Jika user tidak membalas sampai `SESSION_TIMEOUT_SECONDS`, bot otomatis mengirim pemberitahuan timeout dan sesi diakhiri.

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

Saat handoff aktif:

- Bot mengirim notifikasi ke semua nomor di `ADMIN_NUMBERS`.
- Bot diam untuk user tersebut.
- User bisa membatalkan dengan `batal`, `batalkan`, `menu`, atau `keluar`.
- Jika admin belum merespons dalam `ADMIN_PICKUP_TIMEOUT_SECONDS`, bot kembali ke menu utama.

Admin menutup handoff dengan mengirim ke nomor bot:

```text
selesai 628xxxxxxxx
```

Jika admin hanya mengetik `selesai`, bot akan membalas format yang benar. Admin tidak akan mendapat pesan pembuka user. Setelah `selesai <nomor_user>`, user dikabari bahwa bot aktif kembali, lalu mendapat salam dan menu utama.

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

- Session user umum: `SESSION_TIMEOUT_SECONDS=600` atau 10 menit.
- Tunggu admin: `ADMIN_PICKUP_TIMEOUT_SECONDS=300` atau 5 menit.
- Admin handoff stuck: jika admin tidak merespons dalam 30 menit, bot otomatis mengaktifkan diri kembali untuk user dan mengirim pemberitahuan.

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

## Referensi

- [GOWA GitHub](https://github.com/aldinokemal/go-whatsapp-web-multidevice)
- [GOWA webhook payload](https://github.com/aldinokemal/go-whatsapp-web-multidevice/blob/main/docs/webhook-payload.md)
- [BPS WebAPI](https://webapi.bps.go.id/)
