# Product Specification: Marawa BPS Padang Pariaman

## 1. Ringkasan Produk

Marawa BPS Padang Pariaman adalah chatbot WhatsApp untuk layanan data statistik BPS Kabupaten Padang Pariaman. Produk ini membantu masyarakat mencari data dari BPS WebAPI domain `1306`, mendapatkan arahan konsultasi statistik, dan menghubungi admin jika butuh bantuan manusia.

Bot dirancang agar terasa seperti percakapan natural dengan AI, tetapi tetap dibatasi pada lingkup pelayanan data statistik. Model AI digunakan sebagai parser dan perencana percakapan, bukan sebagai sumber kebenaran data. Semua data final harus berasal dari BPS WebAPI atau informasi layanan yang sudah dikonfigurasi.

## 2. Tujuan

- Mempermudah pengguna mencari data statistik BPS Kabupaten Padang Pariaman lewat WhatsApp.
- Mengurangi kebutuhan format menu kaku dengan menerima bahasa natural.
- Memberikan jawaban data yang jelas, termasuk judul data, tahun, satuan, rincian, dan sumber.
- Menjaga keamanan layanan dari prompt injection, jailbreak, permintaan rahasia, dan instruksi di luar domain.
- Menyediakan handoff ke admin ketika bot tidak cukup membantu.
- Mencatat aktivitas pengguna dan percakapan ke Google Spreadsheet untuk monitoring layanan.

## 3. Non-Tujuan

- Bot bukan pengganti website resmi BPS secara penuh.
- Bot tidak menjawab topik umum di luar statistik dan layanan BPS Padang Pariaman.
- Bot tidak mengambil data dari sumber tidak resmi.
- Bot tidak menjalankan command, membaca file server, membuka `.env`, atau mengakses secret.
- Bot tidak melakukan relay penuh percakapan admin-user di versi awal.

## 4. Target Pengguna

- Masyarakat umum yang membutuhkan data statistik Kabupaten Padang Pariaman.
- Mahasiswa, peneliti, jurnalis, dan perangkat daerah yang mencari data cepat.
- Petugas/admin BPS yang menerima eskalasi percakapan dari bot.

## 5. Layanan Utama

Setiap sesi baru, bot wajib memperkenalkan diri dan menjelaskan layanan:

1. Mencari data statistik BPS Kabupaten Padang Pariaman.
2. Rekomendasi dan konsultasi statistik.
3. Menghubungkan Anda dengan admin.
4. Mengakhiri percakapan.

Intro muncul satu kali di awal sesi. Jika sesi timeout atau container restart dan pengguna mulai lagi, intro muncul kembali. Jika pengguna tidak membalas sampai batas timeout, bot mengirim pemberitahuan bahwa sesi sebelumnya diakhiri dan state sesi menjadi `ENDED`. Pesan pertama pada sesi baru selalu hanya membuka sesi dengan greeting/menu; pesan tersebut belum diproses sebagai permintaan data, konsultasi, admin, menu, atau keluar.

## 6. Prinsip Percakapan

- Pengguna boleh mengetik angka, kata kunci, atau kalimat natural.
- Bot tidak memaksa format menu jika maksud pengguna sudah jelas.
- Jika permintaan data terlalu umum, bot menampilkan pilihan data yang mirip dan meminta pengguna memilih nomor.
- Jika pengguna sudah menyebut tahun di pesan awal, bot menyimpan tahun tersebut dan memakainya setelah variabel data dipilih.
- Jika data tahun tertentu tidak tersedia, bot menjelaskan tahun yang tidak tersedia dan tahun yang berhasil ditampilkan.
- Jika pengguna bertanya lanjutan seperti "2022-2025 mana", bot memakai konteks data terakhir.
- Jika pengguna mengetik `menu`, `batal`, atau `batalkan`, bot kembali ke menu utama dan membersihkan konteks data aktif.
- Jika pengguna mengetik `keluar`, bot mengakhiri percakapan.

## 7. User Journey

### 7.1 Sesi Baru

1. Pengguna mengirim pesan apa pun.
2. Bot membalas dengan intro layanan.
3. Bot menunggu pesan berikutnya untuk memproses intent layanan.

Contoh:

```text
User: butuh data tpt laki-laki 2020-2021
Bot: Halo, saya Marawa BPS Padang Pariaman.
Saya bisa membantu layanan berikut:
1. Mencari data statistik BPS Kabupaten Padang Pariaman
2. Rekomendasi dan konsultasi statistik
3. Menghubungkan Anda dengan admin
4. Mengakhiri percakapan

User: butuh data tpt laki-laki 2020-2021
Berikut beberapa data yang mirip. Mana yang Anda maksud?
...
```

### 7.2 Permintaan Data

1. Pengguna meminta data.
2. Guarded agent mengekstrak intent, keyword, tahun, dan periode.
3. Engine mencari variabel BPS melalui `BpsClient`.
4. Jika ada beberapa kandidat, bot menampilkan pilihan bernomor.
5. Pengguna memilih nomor atau mengetik keyword baru.
6. Bot meminta tahun jika belum ada.
7. Bot mengambil data dan menampilkan tabel teks.

### 7.3 Data Tidak Ditemukan

Jika keyword tidak menghasilkan data:

- Bot meminta pengguna mengetik keyword lain yang lebih spesifik.
- Bot tetap berada di sub-sesi pencarian data.
- Pengguna dapat mengetik `menu` untuk keluar dari sub-sesi.

### 7.4 Tahun Tidak Tersedia

Jika sebagian tahun tidak tersedia:

- Bot tetap menampilkan tahun yang tersedia.
- Bot menambahkan catatan tahun yang belum tersedia.
- Konteks data terakhir disimpan untuk menjawab follow-up.

Jika semua tahun tidak tersedia:

- Bot memberi daftar tahun tersedia.
- Bot tetap berada di tahap meminta tahun.

### 7.5 Konsultasi Statistik

Jika pengguna meminta konsultasi/rekomendasi statistik:

- Bot mengirim link `https://s.bps.go.id/tamu1306`.
- Bot menawarkan bantuan admin jika pengguna perlu dibantu petugas.

### 7.6 Handoff Admin

1. Pengguna meminta admin.
2. Session masuk mode `WAITING_ADMIN`.
3. Bot mengirim notifikasi ke seluruh nomor admin.
4. Bot berhenti membalas pengguna selama handoff aktif.
5. Pengguna tetap bisa membatalkan dengan `batal`, `batalkan`, `menu`, atau `keluar`.
6. Jika admin tidak mengambil alih sampai timeout, bot mengembalikan pengguna ke menu.
7. Admin dapat mengaktifkan bot kembali dengan command `selesai <nomor_user>`.

## 8. Arsitektur

```text
WhatsApp User
  -> GOWA WhatsApp Gateway
  -> FastAPI /webhook/gowa
  -> SessionStore
  -> ConversationEngine
  -> GuardedDataAgent
  -> AiClient
  -> BpsClient / AdminHandoff / GoogleSheetsLogger
  -> GowaClient.send_text()
```

Komponen:

- `GOWA`: WhatsApp gateway.
- `FastAPI`: HTTP service bot.
- `ConversationEngine`: state machine dan eksekutor percakapan.
- `GuardedDataAgent`: planner terbatas berbasis allowlist.
- `AiClient`: adapter model AI dan structured parser.
- `BpsClient`: pencarian variabel, metadata, tahun/periode, dan data BPS WebAPI.
- `GoogleSheetsLogger`: logging pengguna dan percakapan.
- `AdminHandoffService`: eskalasi ke admin.
- `SessionStore`: session in-memory versi awal.

## 9. Guarded Agent

Guarded agent memakai model “agentic but constrained”. Agent hanya boleh memilih aksi dari allowlist:

- `ask_data_query`
- `search_bps_variables`
- `show_consultation`
- `handoff_admin`
- `exit`
- `show_menu`
- `clarify`
- `reject_unsafe`
- `reject_out_of_scope`

Agent tidak mengeksekusi tool secara langsung. Engine Python tetap menjadi pengambil keputusan akhir dan eksekutor aksi.

## 10. Keamanan AI

AI dianggap tidak tepercaya. Output AI harus melewati validasi aplikasi.

Mitigasi:

- Output AI berupa JSON terstruktur.
- Intent hanya boleh enum yang dikenal.
- Tool/action hanya boleh dari allowlist.
- Query dibersihkan dari pola injection dan karakter tidak perlu.
- Permintaan membuka system prompt, secret, token, `.env`, command, Docker, shell, atau instruksi jailbreak ditolak.
- AI tidak menerima credential, API key, atau secret.
- AI tidak dapat mengirim pesan sendiri tanpa validasi engine.
- AI tidak dapat menjalankan command atau membaca file.
- Jika output AI rusak, kosong, atau tidak valid, sistem fallback ke rule parser.

Pesan di luar domain layanan statistik ditolak dengan sopan dan diarahkan kembali ke layanan BPS.

## 11. Data BPS

Domain default: `1306`.

Proses data:

1. AI/parser mengekstrak query utama, beberapa variasi keyword, tahun, dan periode.
2. Sistem menambah rewrite lokal untuk istilah awam dan singkatan, misalnya `kerjaan`, `ipm`, `tpt`, `tpak`, dan `pdrb`.
3. Keyword dicari lewat endpoint variabel BPS, SIMDASI, publikasi, dan index/cache variabel lokal.
4. Sistem mencari ke tiga sumber: tabel dinamis, SIMDASI, dan publikasi, lalu menampilkan hasilnya per kelompok sumber.
5. Hasil diranking memakai exact match, acronym match, sinonim, fuzzy similarity, dan pilihan user sebelumnya.
6. Jika hasil banyak, sistem menampilkan 5 opsi pertama dengan label sumber di judul, misalnya `[Tabel Dinamis] Jumlah Penduduk`.
7. Pengguna dapat mengetik `lainnya` untuk hasil berikutnya atau `sebelumnya` untuk kembali.
8. Setelah variabel tabel dinamis atau SIMDASI dipilih, sistem membaca dimensi tahun, periode, wilayah/rincian, dan kategori.
9. Data tabel dinamis ditampilkan sebagai tabel teks dengan judul berlabel `[Tabel Dinamis]`.
10. Jika fallback SIMDASI punya isi tabel yang bisa dibaca dari endpoint `view`, bot menampilkannya sebagai tabel teks dengan judul berlabel `[SIMDASI]`.
11. Jika publikasi dipilih, bot langsung mengirim judul berlabel `[Publikasi]`, tanggal rilis, ringkasan lengkap jika ada, dan link halaman detail publikasi BPS tanpa menanyakan tahun lagi; publikasi tidak dipaksa menjadi tabel karena bentuknya dokumen.
12. Balasan data tidak dipotong oleh formatter aplikasi. Jika terlalu panjang untuk satu pesan WhatsApp, gateway client membaginya menjadi beberapa pesan berurutan tanpa menghapus isi.

Jawaban data wajib menyebut:

- Judul data.
- Satuan.
- Tabel rincian dan tahun/periode.
- Sumber: BPS Kabupaten Padang Pariaman via WebAPI.
- Catatan tahun tidak tersedia jika ada.

URL sumber WebAPI dan link PDF/download mentah tidak ditampilkan ke pengguna karena bentuknya membingungkan dan bukan halaman publik yang ramah pengguna.

## 12. Google Spreadsheet Logging

Sheet `users`:

- Nomor WhatsApp.
- Nama WhatsApp.
- Pertama kali interaksi.
- Terakhir aktif.
- Total sesi.
- Status terakhir.

Sheet `conversations`:

- Timestamp.
- Nomor.
- Nama.
- Arah pesan.
- State.
- Intent.
- Isi pesan user.
- Respons bot.
- Metadata.
- Source URL jika ada.

Google Sheets bersifat graceful. Jika credential kosong atau API gagal, bot tetap membalas pengguna.

## 13. Konfigurasi

Konfigurasi melalui `.env`:

```env
BPS_API_KEY=
BPS_DOMAIN=1306
BPS_CACHE_TTL_SECONDS=3600

AI_PROVIDER=openai
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_MODEL=llama3.1

GOWA_BASE_URL=http://gowa:3000
GOWA_BASIC_AUTH_USER=marawa
GOWA_BASIC_AUTH_PASS=
GOWA_WEBHOOK_SECRET=

ADMIN_NUMBERS=628xxxx,628yyyy
GOOGLE_SHEETS_SPREADSHEET_ID=
GOOGLE_SERVICE_ACCOUNT_JSON=

SESSION_TIMEOUT_SECONDS=600
ADMIN_PICKUP_TIMEOUT_SECONDS=300
```

## 14. Public Interface

HTTP:

- `GET /health`
- `POST /webhook/gowa`

Webhook:

- Hanya event `message` yang diproses.
- Pesan outgoing/fromMe diabaikan.
- Duplicate inbound diabaikan.
- Echo dari pesan bot baru dikirim diabaikan.
- Signature webhook divalidasi dengan secret.

Command user:

- `menu`
- `batal`
- `batalkan`
- `keluar`
- `lainnya`
- `sebelumnya`

Command admin:

- `selesai <nomor_user>`

## 15. State Session

State utama:

- `MAIN_MENU`
- `ASKING_DATA_QUERY`
- `CONFIRMING_DATA_VARIABLE`
- `ASKING_DATA_YEAR`
- `WAITING_ADMIN`
- `ENDED`

Session menyimpan:

- Query data aktif.
- Kandidat variabel BPS.
- Halaman opsi saat ini.
- Variabel BPS terpilih.
- Tahun/periode pending.
- Konteks data terakhir.
- Flag `needs_intro`.

## 16. Caching

`BpsClient` memakai cache in-memory untuk respons BPS WebAPI.

Default:

- TTL: `3600` detik.
- Dapat dimatikan dengan `BPS_CACHE_TTL_SECONDS=0`.

Tujuan cache:

- Mempercepat pencarian metadata.
- Mengurangi request berulang ke BPS WebAPI.
- Mengurangi risiko rate limit atau respons lambat.

## 17. Acceptance Criteria

- Sesi baru selalu mendapat intro layanan.
- Intro hanya muncul sekali dalam satu sesi aktif.
- Setelah session timeout, intro muncul lagi.
- User dapat meminta data dengan kalimat natural.
- User dapat memilih opsi data dengan nomor.
- User dapat melihat opsi lanjutan dengan `lainnya`.
- User dapat kembali ke opsi sebelumnya dengan `sebelumnya`.
- Tahun dari pesan awal disimpan dan dipakai setelah variabel dipilih.
- Data tahunan dan triwulanan didukung.
- Jika tahun tidak tersedia, bot tidak reset ke menu.
- Follow-up tentang tahun hilang dijawab berdasarkan konteks data terakhir.
- Prompt injection/jailbreak ditolak.
- Pesan di luar domain layanan ditolak dengan sopan.
- Handoff admin berjalan dan dapat dibatalkan.
- Logging Google Sheets gagal tidak menghentikan bot.
- Unit test berjalan sukses lewat Docker.

## 18. Rencana Pengembangan

- Session store persisten dengan Redis atau database.
- Admin dashboard untuk melihat percakapan aktif.
- Mode relay admin-user penuh.
- Evaluasi kualitas pencarian BPS dengan dataset pertanyaan nyata.
- Summarizer jawaban data yang lebih natural tetapi tetap grounding ke payload BPS.
- Observability: request ID, latency BPS, cache hit ratio, dan error dashboard.
- Rate limiting per nomor untuk mengurangi spam.
- Export percakapan dan statistik penggunaan layanan.
