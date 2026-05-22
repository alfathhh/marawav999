# BPS Dynamic Table + Publication Chatbot Architecture (Python)

## Goal

Membuat chatbot AI yang bisa menerima pertanyaan natural seperti:

- "Data PDRB Padang Pariaman terbaru"
- "Jumlah penduduk sekarang"
- "Indeks kesetaraan gender tahun ini"

Lalu bot otomatis:

1. memahami intent user,
2. mencari indikator BPS yang cocok,
3. resolve parameter Dynamic Table,
4. mengambil data dari Web API BPS,
5. fallback ke Publication jika perlu,
6. menjawab dalam bahasa natural.

---

# Arsitektur Sistem

```text
User (WhatsApp / Telegram / Web)
        ↓
Python Chatbot
        ↓
AI Intent Extractor
        ↓
BPS Resolver Engine
    ├── Dynamic Table
    └── Publication Fallback
        ↓
Response Formatter
        ↓
Bot Reply
```

---

# Konsep Penting

User TIDAK perlu tahu parameter API BPS.

User cukup bilang:

```text
"PDRB Padang Pariaman terbaru"
```

Bot yang mencari sendiri:

- var
- th
- turvar
- vervar
- turth

---

# Dynamic Table Flow

## 1. User Query

Contoh:

```text
"PDRB Padang Pariaman terbaru"
```

---

## 2. AI Extract Intent

AI mengubah query menjadi struktur:

```json
{
  "keyword": "PDRB",
  "wilayah": "Padang Pariaman",
  "domain": "1306",
  "tahun": "terbaru"
}
```

---

## 3. Cari Variabel Dynamic Table

Gunakan:

```text
model=var
```

Tujuan:

- mencari indikator BPS yang paling relevan,
- mendapatkan `var_id`.

---

## 4. Resolve Parameter Dynamic Table

Setelah dapat `var_id`, ambil:

| Model | Fungsi |
|---|---|
| th | daftar tahun |
| turth | turunan waktu |
| vervar | wilayah |
| turvar | kategori turunan |

---

## 5. Pilih Default Parameter

Bot otomatis memilih:

| Parameter | Default |
|---|---|
| th | tahun terbaru |
| turth | total/tahunan |
| vervar | Padang Pariaman |
| turvar | total |

---

## 6. Request Data

Gunakan:

```text
model=data
```

Dengan parameter lengkap:

```text
var
th
turth
vervar
turvar
```

---

# Publication Fallback

Jika Dynamic Table gagal:

- keyword ambigu,
- data kosong,
- indikator tidak tersedia,

maka bot mencari publication.

Gunakan:

```text
model=publication
```

---

# Recommended Folder Structure

```text
project/
│
├── app.py
├── config.py
│
├── services/
│   ├── bps_api.py
│   ├── dynamic_table.py
│   ├── publication.py
│   └── ai_parser.py
│
├── utils/
│   ├── scoring.py
│   └── formatter.py
│
├── prompts/
│   └── intent_prompt.txt
│
└── requirements.txt
```

---

# config.py

```python
BPS_KEY = "YOUR_BPS_API_KEY"
DOMAIN = "1306"

BASE_URL = "https://webapi.bps.go.id/v1/api/list"
```

---

# Base API Service

## services/bps_api.py

```python
import requests
from config import BASE_URL, BPS_KEY, DOMAIN

def bps_list(model, **params):
    query = {
        "model": model,
        "lang": "ind",
        "domain": DOMAIN,
        "key": BPS_KEY,
        **params
    }

    response = requests.get(BASE_URL, params=query, timeout=30)
    response.raise_for_status()

    return response.json()
```

---

# Helper Function

```python
def rows(resp):
    if resp.get("data-availability") != "available":
        return []

    data = resp.get("data", [])

    if isinstance(data, list) and len(data) > 1:
        return data[1]

    return []
```

---

# Variable Search Engine

```python
from difflib import SequenceMatcher
from services.bps_api import bps_list

def similarity(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def search_variable(keyword, max_page=5):
    results = []

    for page in range(1, max_page + 1):
        resp = bps_list(
            "var",
            page=page,
            area=1
        )

        for item in rows(resp):
            title = item.get("title", "")

            results.append({
                "score": similarity(keyword, title),
                "var_id": item.get("var_id"),
                "title": title,
            })

    results.sort(key=lambda x: x["score"], reverse=True)

    return results[:10]
```

---

# Dynamic Parameter Resolver

```python
def pick_latest_year(items):
    return sorted(
        items,
        key=lambda x: int(x["label"]),
        reverse=True
    )[0]

def pick_total_or_first(items):
    if not items:
        return None

    total_words = [
        "total",
        "jumlah",
        "semua",
        "all"
    ]

    for item in items:
        label = item.get("label", "").lower()

        if any(word in label for word in total_words):
            return item

    return items[0]
```

---

# Full Resolver Example

```python
def resolve_dynamic_params(keyword):
    vars_found = search_variable(keyword)

    if not vars_found:
        return None

    best_var = vars_found[0]

    return {
        "var": best_var["var_id"]
    }
```

---

# Main Chatbot Logic

```python
def chatbot(user_message):
    params = resolve_dynamic_params(user_message)

    if params:
        return {
            "source": "dynamic_table",
            "params": params
        }

    return {
        "source": "publication"
    }
```

---

# Recommended Tech Stack

| Layer | Tech |
|---|---|
| Chatbot | Python |
| AI | OpenAI / Ollama |
| API | Requests |
| Cache | Redis |
| Messaging | WhatsApp |
| Automation | n8n |
| Vector Search | FAISS |

---

# Important Notes

AI tidak boleh mengarang data statistik.

Semua angka HARUS berasal dari API BPS.
