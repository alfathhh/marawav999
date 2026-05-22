import pytest
import respx
from httpx import Response
from urllib.parse import quote

from app.services.bps_client import BpsClient


def mock_variable_pages(title="Jumlah Penduduk", var_id=123):
    for page in range(1, 6):
        payload = {"data-availability": "available", "data": [1, [{"var_id": var_id, "title": title, "unit": "jiwa"}] if page == 1 else []]}
        respx.get(f"https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/{page}/area/1/key/key").mock(
            return_value=Response(200, json=payload)
        )


def mock_keyword_variable_search(keyword, rows=None, key="key", status_code=200):
    safe_keyword = quote(keyword)
    rows = rows or []
    respx.get(f"https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/1/keyword/{safe_keyword}/area/1/key/{key}").mock(
        return_value=Response(status_code, json={"data-availability": "available", "data": [len(rows), rows]})
    )
    if status_code >= 500 or status_code == 403:
        respx.get(f"https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/1/keyword/{safe_keyword}/key/{key}").mock(
            return_value=Response(status_code, json={"error": "server error"})
        )
    if status_code >= 500:
        respx.get("https://webapi.bps.go.id/v1/api/list").mock(
            return_value=Response(status_code, json={"error": "server error"})
        )


def mock_empty_variable_pipeline(client, query, keywords=None):
    for keyword in client._candidate_queries(query, keywords):
        mock_keyword_variable_search(keyword)
    for page in range(1, 6):
        respx.get(f"https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/{page}/area/1/key/key").mock(
            return_value=Response(200, json={"data-availability": "available", "data": [0, []]})
        )


def mock_simdasi_search(keyword, rows=None, key="key"):
    safe_keyword = quote(keyword)
    rows = rows or []
    respx.get(f"https://webapi.bps.go.id/v1/api/list/model/statictable/perpage/100000/lang/ind/domain/1306/key/{key}/keyword/{safe_keyword}/page/1").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [len(rows), rows]})
    )


def mock_simdasi_searches(client, query, rows_by_keyword=None, keywords=None, key="key"):
    rows_by_keyword = rows_by_keyword or {}
    for keyword in client._candidate_queries(query, keywords):
        mock_simdasi_search(keyword, rows_by_keyword.get(keyword, []), key=key)


def mock_publication_search(keyword, rows=None, key="key"):
    safe_keyword = quote(keyword)
    rows = rows or []
    respx.get(f"https://webapi.bps.go.id/v1/api/list/model/publication/lang/ind/domain/1306/keyword/{safe_keyword}/key/{key}").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [len(rows), rows]})
    )


def mock_empty_content_searches(client, query, keywords=None):
    for keyword in client._candidate_queries(query, keywords):
        mock_simdasi_search(keyword)
        mock_publication_search(keyword)


def mock_simdasi_view(table_id, payload=None, key="key"):
    payload = payload or {"data-availability": "available", "data": []}
    respx.get(f"https://webapi.bps.go.id/v1/api/view/model/statictable/lang/ind/domain/1306/id/{table_id}/key/{key}/").mock(
        return_value=Response(200, json=payload)
    )


def mock_dynamic_params(var_id=123):
    respx.get(f"https://webapi.bps.go.id/v1/api/list/model/th/lang/ind/domain/1306/var/{var_id}/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [2, [{"id": 2024, "label": "2024"}, {"id": 2025, "label": "2025"}]]})
    )
    respx.get(f"https://webapi.bps.go.id/v1/api/list/model/turth/lang/ind/domain/1306/var/{var_id}/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [1, [{"id": 0, "label": "Tahunan"}]]})
    )
    respx.get(f"https://webapi.bps.go.id/v1/api/list/model/vervar/lang/ind/domain/1306/var/{var_id}/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [1, [{"id": 1306, "label": "Padang Pariaman"}]]})
    )
    respx.get(f"https://webapi.bps.go.id/v1/api/list/model/turvar/lang/ind/domain/1306/var/{var_id}/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [1, [{"id": 0, "label": "Total"}]]})
    )


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_found_with_resolved_dynamic_table_source_url():
    client = BpsClient("key", "1306")
    mock_keyword_variable_search("penduduk padang pariaman terbaru")
    mock_variable_pages()
    mock_dynamic_params()
    respx.get("https://webapi.bps.go.id/v1/api/list/model/data/lang/ind/domain/1306/var/123/th/2025/turth/0/vervar/1306/turvar/0/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [{"tahun": "2025", "value": 1000}]})
    )

    result = await client.search_data("penduduk padang pariaman terbaru")

    assert result.found
    assert result.source_url
    assert "/var/123/th/2025/turth/0/vervar/1306/turvar/0/" in result.source_url
    assert "[Tabel Dinamis] Jumlah Penduduk" in result.summary
    assert "Jumlah Penduduk" in result.summary
    assert "1000" in result.summary
    assert result.metadata["params"]["th"] == "2025"


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_formats_datacontent_without_metadata_val_noise():
    client = BpsClient("key", "1306")
    mock_keyword_variable_search("penduduk")
    mock_variable_pages(var_id=29)
    respx.get("https://webapi.bps.go.id/v1/api/list/model/th/lang/ind/domain/1306/var/29/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [1, [{"val": 125, "label": "2025"}]]})
    )
    respx.get("https://webapi.bps.go.id/v1/api/list/model/turth/lang/ind/domain/1306/var/29/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [1, [{"val": 0, "label": "Tahunan"}]]})
    )
    respx.get("https://webapi.bps.go.id/v1/api/list/model/vervar/lang/ind/domain/1306/var/29/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [0, []]})
    )
    respx.get("https://webapi.bps.go.id/v1/api/list/model/turvar/lang/ind/domain/1306/var/29/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [1, [{"val": 27, "label": "Total"}]]})
    )
    respx.get("https://webapi.bps.go.id/v1/api/list/model/data/lang/ind/domain/1306/var/29/th/125/turth/0/vervar/0/turvar/27/key/key").mock(
        return_value=Response(200, json={
            "data-availability": "available",
            "data": [
                {"val": 29, "label": "Jumlah Penduduk"},
                {"tahun": [{"val": 125, "label": "2025"}]},
                {"datacontent": {"29125270": 519}},
            ],
        })
    )

    result = await client.search_data("penduduk")

    assert result.found
    assert "Tahun: 2025" in result.summary
    assert "- 519" in result.summary
    assert "- 29" not in result.summary
    assert "- 27" not in result.summary
    assert "/vervar//" not in result.source_url


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_publication_fallback_when_variable_not_found():
    client = BpsClient("key", "1306")
    mock_keyword_variable_search("penduduk")
    mock_simdasi_searches(client, "penduduk")
    for page in range(1, 6):
        respx.get(f"https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/{page}/area/1/key/key").mock(
            return_value=Response(200, json={"data-availability": "available", "data": [0, []]})
        )
    respx.get("https://webapi.bps.go.id/v1/api/list/model/publication/lang/ind/domain/1306/keyword/penduduk/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [1, [{"title": "Kabupaten Padang Pariaman Dalam Angka", "rl_date": "2025-02-28", "pdf": "https://example.test/pub.pdf"}]]})
    )

    result = await client.search_data("penduduk")

    assert result.found
    assert result.source_url == "https://padangpariamankab.bps.go.id/id/publication"
    assert "publikasi BPS yang terkait" in result.summary
    assert "[Publikasi] Kabupaten Padang Pariaman Dalam Angka" in result.summary
    assert "Link publikasi: https://padangpariamankab.bps.go.id/id/publication" in result.summary
    assert "pub.pdf" not in result.summary


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_not_found_after_publication_fallback():
    client = BpsClient("key", "1306")
    mock_keyword_variable_search("penduduk")
    mock_simdasi_searches(client, "penduduk")
    for page in range(1, 6):
        respx.get(f"https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/{page}/area/1/key/key").mock(
            return_value=Response(200, json={"data-availability": "available", "data": [0, []]})
        )
    respx.get("https://webapi.bps.go.id/v1/api/list/model/publication/lang/ind/domain/1306/keyword/penduduk/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [0, []]})
    )

    result = await client.search_data("penduduk")

    assert not result.found


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_publication_server_error_does_not_crash_content_search():
    client = BpsClient("key", "1306")
    mock_empty_variable_pipeline(client, "kecamatan dalam angka", [])
    for keyword in client._candidate_queries("kecamatan dalam angka", [])[:8]:
        mock_simdasi_search(keyword)
        safe_keyword = quote(keyword)
        respx.get(f"https://webapi.bps.go.id/v1/api/list/model/publication/lang/ind/domain/1306/keyword/{safe_keyword}/key/key").mock(
            return_value=Response(500, json={"error": "server error"})
        )
    respx.get("https://webapi.bps.go.id/v1/api/list").mock(
        return_value=Response(500, json={"error": "server error"})
    )

    result = await client.search_variable_options("kecamatan dalam angka", keywords=[])

    assert not result.found
    assert "Data belum ditemukan" in result.summary


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_http_error():
    client = BpsClient("bad", "1306")
    mock_keyword_variable_search("penduduk", key="bad", status_code=403)
    respx.get("https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/1/area/1/key/bad").mock(
        return_value=Response(403, json={"error": "invalid"})
    )

    result = await client.search_data("penduduk")

    assert not result.found
    assert "key" in result.summary.lower() or "webapi" in result.summary.lower()


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_retries_var_search_without_area_after_server_error():
    client = BpsClient("key", "1306")
    mock_keyword_variable_search("penduduk", status_code=500)
    mock_simdasi_searches(client, "penduduk")
    respx.get("https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/1/area/1/key/key").mock(
        return_value=Response(500, json={"error": "server error"})
    )
    respx.get("https://webapi.bps.go.id/v1/api/list").mock(
        return_value=Response(500, json={"error": "server error"})
    )
    respx.get("https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/1/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [0, []]})
    )
    for page in range(2, 6):
        respx.get(f"https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/{page}/area/1/key/key").mock(
            return_value=Response(200, json={"data-availability": "available", "data": [0, []]})
        )
    respx.get("https://webapi.bps.go.id/v1/api/list/model/publication/lang/ind/domain/1306/keyword/penduduk/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [0, []]})
    )

    result = await client.search_data("penduduk")

    assert not result.found


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_retries_var_search_without_area_after_forbidden_area():
    client = BpsClient("key", "1306")
    safe_keyword = quote("penduduk")
    respx.get(f"https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/1/keyword/{safe_keyword}/area/1/key/key").mock(
        return_value=Response(403, json={"error": "forbidden area"})
    )
    respx.get(f"https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/1/keyword/{safe_keyword}/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [1, [{"var_id": 1, "title": "Jumlah Penduduk", "unit": "jiwa"}]]})
    )
    mock_empty_content_searches(client, "penduduk")
    for page in range(1, 6):
        rows = [{"var_id": 1, "title": "Jumlah Penduduk", "unit": "jiwa"}] if page == 1 else []
        respx.get(f"https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/{page}/area/1/key/key").mock(
            return_value=Response(200, json={"data-availability": "available", "data": [len(rows), rows]})
        )

    result = await client.search_variable_options("penduduk")

    assert result.found
    assert "[Tabel Dinamis] Jumlah Penduduk" in result.summary


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_hides_failed_simdasi_source_when_dynamic_options_exist():
    client = BpsClient("key", "1306")
    mock_keyword_variable_search("penduduk", [{"var_id": 1, "title": "Jumlah Penduduk", "unit": "jiwa"}])
    for page in range(1, 6):
        rows = [{"var_id": 1, "title": "Jumlah Penduduk", "unit": "jiwa"}] if page == 1 else []
        respx.get(f"https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/{page}/area/1/key/key").mock(
            return_value=Response(200, json={"data-availability": "available", "data": [len(rows), rows]})
        )
    for keyword in client._candidate_queries("penduduk", None)[:8]:
        respx.get(f"https://webapi.bps.go.id/v1/api/list/model/statictable/perpage/100000/lang/ind/domain/1306/key/key/keyword/{quote(keyword)}/page/1").mock(
            return_value=Response(403, json={"error": "simdasi forbidden"})
        )
        mock_publication_search(keyword)

    result = await client.search_variable_options("penduduk")

    assert result.found
    assert "[Tabel Dinamis] Jumlah Penduduk" in result.summary
    assert "SIMDASI" not in result.summary


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_uses_simdasi_before_publication_when_variable_not_found():
    client = BpsClient("key", "1306")
    mock_keyword_variable_search("ketenagakerjaan")
    for page in range(1, 6):
        respx.get(f"https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/{page}/area/1/key/key").mock(
            return_value=Response(200, json={"data-availability": "available", "data": [0, []]})
        )
    mock_simdasi_search(
        "ketenagakerjaan",
        [{"id": 88, "title": "Statistik Ketenagakerjaan Kabupaten Padang Pariaman", "updated_at": "2025-01-01"}],
    )
    mock_simdasi_view(88)

    result = await client.search_data("ketenagakerjaan")

    assert result.found
    assert "SIMDASI" in result.summary
    assert "[SIMDASI] Statistik Ketenagakerjaan" in result.summary
    assert "Statistik Ketenagakerjaan" in result.summary
    assert "publication" not in result.metadata
    assert result.metadata["simdasi"]["id"] == 88


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_formats_simdasi_view_as_table_when_available():
    client = BpsClient("key", "1306")
    mock_keyword_variable_search("ketenagakerjaan")
    for page in range(1, 6):
        respx.get(f"https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/{page}/area/1/key/key").mock(
            return_value=Response(200, json={"data-availability": "available", "data": [0, []]})
        )
    mock_simdasi_search("ketenagakerjaan", [{"id": 88, "title": "Statistik Ketenagakerjaan", "updated_at": "2025-01-01"}])
    mock_simdasi_view(
        88,
        {
            "data-availability": "available",
            "data": {
                "table": [
                    ["Uraian", "2023", "2024"],
                    ["Angkatan Kerja", "100", "110"],
                    ["Bekerja", "95", "104"],
                ]
            },
        },
    )

    result = await client.search_data("ketenagakerjaan")

    assert result.found
    assert "[SIMDASI] Statistik Ketenagakerjaan" in result.summary
    assert "```text" in result.summary
    assert "Uraian         | 2023 | 2024" in result.summary
    assert "Angkatan Kerja | 100  | 110" in result.summary
    assert "SIMDASI WebAPI" in result.summary


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_asks_clarification_for_broad_population_query():
    client = BpsClient("key", "1306")
    mock_keyword_variable_search("jumlah penduduk")
    for page in range(1, 6):
        rows = [
            {"var_id": 1, "title": "Jumlah Penduduk", "unit": "jiwa"},
            {"var_id": 2, "title": "Penduduk Menurut Jenis Kelamin", "unit": "jiwa"},
        ] if page == 1 else []
        respx.get(f"https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/{page}/area/1/key/key").mock(
            return_value=Response(200, json={"data-availability": "available", "data": [len(rows), rows]})
        )

    result = await client.search_data("jumlah penduduk")

    assert result.found
    assert result.too_many
    assert "1. [Tabel Dinamis] Jumlah Penduduk" in result.summary
    assert "ketik nomor" in result.summary.lower()
    assert "kata kunci yang lebih detail" in result.summary


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_uses_bps_keyword_search_for_pdrb():
    client = BpsClient("key", "1306")
    mock_empty_content_searches(client, "pdrb")
    pdrb_rows = [
        {"var_id": 163, "title": "Produk Domestik Regional Bruto (PDRB) Atas Dasar Harga Berlaku ", "unit": "Juta Rupiah"},
        {"var_id": 164, "title": "Produk Domestik Regional Bruto (PDRB) Atas Dasar Harga Konstan", "unit": "Juta Rupiah"},
        {"var_id": 167, "title": "Laju Pertumbuhan Produk Domestik Regional Bruto (PDRB) Atas Dasar Harga Konstan", "unit": "Persen"},
    ]
    for keyword in ["pdrb", "produk domestik regional bruto"]:
        mock_keyword_variable_search(keyword, pdrb_rows)
    for page in range(1, 6):
        rows = [{"var_id": 1, "title": "Jumlah Penduduk", "unit": "jiwa"}] if page == 1 else []
        respx.get(f"https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/{page}/area/1/key/key").mock(
            return_value=Response(200, json={"data-availability": "available", "data": [len(rows), rows]})
        )

    result = await client.search_variable_options("pdrb")

    assert result.found
    assert "Produk Domestik Regional Bruto" in result.summary
    assert "[Tabel Dinamis] Produk Domestik Regional Bruto" in result.summary
    assert "Jumlah Penduduk" not in result.summary


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_prefers_publication_for_dalam_angka_query():
    client = BpsClient("key", "1306")
    mock_empty_variable_pipeline(client, "padang pariaman dalam angka", ["publikasi padang pariaman dalam angka"])
    for keyword in ["padang pariaman dalam angka", "publikasi padang pariaman dalam angka", "kabupaten padang pariaman dalam angka", "daerah dalam angka"]:
        mock_simdasi_search(keyword)
    respx.get("https://webapi.bps.go.id/v1/api/list/model/publication/lang/ind/domain/1306/keyword/padang%20pariaman%20dalam%20angka/key/key").mock(
        return_value=Response(
            200,
            json={
                "data-availability": "available",
                "data": [
                    1,
                    [
                        {
                            "id": "632a70da42c6c2f59eb034ce",
                            "title": "Kabupaten Padang Pariaman Dalam Angka 2025",
                            "rl_date": "2025-02-28",
                            "abstract": "Ringkasan panjang tanpa dipotong " * 40,
                            "pdf": "https://webapi.bps.go.id/download.php?f=encrypted",
                        }
                    ],
                ],
            },
        )
    )
    for keyword in ["publikasi padang pariaman dalam angka", "kabupaten padang pariaman dalam angka", "daerah dalam angka"]:
        respx.get(f"https://webapi.bps.go.id/v1/api/list/model/publication/lang/ind/domain/1306/keyword/{quote(keyword)}/key/key").mock(
            return_value=Response(200, json={"data-availability": "available", "data": [0, []]})
        )

    result = await client.search_variable_options(
        "padang pariaman dalam angka",
        keywords=["publikasi padang pariaman dalam angka"],
    )

    assert result.found
    assert result.too_many
    assert "Saya menemukan beberapa hasil yang mungkin sesuai" in result.summary
    assert "1. [Publikasi] Kabupaten Padang Pariaman Dalam Angka 2025" in result.summary
    assert result.metadata["matches"][0]["source_url"] == "https://padangpariamankab.bps.go.id/id/publication/2025/02/28/632a70da42c6c2f59eb034ce/kabupaten-padang-pariaman-dalam-angka-2025.html"
    assert "..." not in result.summary


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_ranks_publication_options_by_query():
    client = BpsClient("key", "1306")
    rows = [
        {"id": "padang", "title": "Kabupaten Padang Pariaman Dalam Angka 2026", "rl_date": "2026-02-27"},
        {"id": "kec", "title": "Kecamatan 2x11 Kayu Tanam Dalam Angka 2026", "rl_date": "2026-09-26"},
    ]
    respx.get("https://webapi.bps.go.id/v1/api/list/model/publication/lang/ind/domain/1306/keyword/kecamatan%20dalam%20angka/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [2, rows]})
    )
    for keyword in ["kabupaten padang pariaman dalam angka", "padang pariaman dalam angka", "daerah dalam angka"]:
        respx.get(f"https://webapi.bps.go.id/v1/api/list/model/publication/lang/ind/domain/1306/keyword/{quote(keyword)}/key/key").mock(
            return_value=Response(200, json={"data-availability": "available", "data": [0, []]})
        )

    result = await client.search_publication_options("kecamatan dalam angka", keywords=[])

    assert result.found
    titles = [item["title"] for item in result.metadata["matches"]]
    assert "Kecamatan 2x11 Kayu Tanam Dalam Angka 2026" in titles
    assert "Kabupaten Padang Pariaman Dalam Angka 2026" in titles


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_uses_source_priority_before_publication_options():
    client = BpsClient("key", "1306")
    mock_empty_content_searches(client, "kecamatan dalam angka", [])
    for keyword in client._candidate_queries("kecamatan dalam angka", []):
        mock_keyword_variable_search(keyword)
    for page in range(1, 6):
        rows = [{"var_id": 9, "title": "Kecamatan Dalam Angka Dynamic", "unit": "-"}] if page == 1 else []
        respx.get(f"https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/{page}/area/1/key/key").mock(
            return_value=Response(200, json={"data-availability": "available", "data": [len(rows), rows]})
        )

    result = await client.search_variable_options("kecamatan dalam angka", keywords=[])

    assert result.found
    assert result.metadata["matches"][0]["source_type"] == "dynamic_table"


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_uses_simdasi_options_before_publication_options():
    client = BpsClient("key", "1306")
    mock_empty_variable_pipeline(client, "kecamatan dalam angka", [])
    for keyword in client._candidate_queries("kecamatan dalam angka", []):
        rows = [{"id": 7, "title": "Tabel Kecamatan Dalam Angka"}] if keyword == "kecamatan dalam angka" else []
        mock_simdasi_search(keyword, rows)
        mock_publication_search(keyword)

    result = await client.search_variable_options("kecamatan dalam angka", keywords=[])

    assert result.found
    assert result.metadata["matches"][0]["source_type"] == "simdasi"


def test_bps_client_formats_selected_publication_after_year():
    client = BpsClient("key", "1306")
    publication = {
        "source_type": "publication",
        "title": "Kecamatan 2x11 Kayu Tanam Dalam Angka 2026",
        "rl_date": "2026-09-26",
        "id": "abc",
        "abstract": "Publikasi kecamatan.",
    }

    result = client._publication_table_result(publication, ["2026"], "1306")

    assert result.found
    assert "[Publikasi] Kecamatan 2x11 Kayu Tanam Dalam Angka 2026" in result.message
    assert "Tahun diminta: 2026" in result.message
    assert "Link publikasi:" in result.message


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_uses_expanded_keyword_search_for_ipm():
    client = BpsClient("key", "1306")
    mock_empty_content_searches(client, "ipm")
    ipm_rows = [{"var_id": 182, "title": "Indeks Pembangunan Manusia (IPM)", "unit": ""}]
    mock_keyword_variable_search("ipm", ipm_rows)
    for keyword in ["indeks pembangunan manusia", "pembangunan manusia"]:
        mock_keyword_variable_search(keyword, ipm_rows)
    for page in range(1, 6):
        respx.get(f"https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/{page}/area/1/key/key").mock(
            return_value=Response(200, json={"data-availability": "available", "data": [0, []]})
        )

    result = await client.search_variable_options("ipm")

    assert result.found
    assert "Indeks Pembangunan Manusia" in result.summary
    assert "[Tabel Dinamis] Indeks Pembangunan Manusia" in result.summary


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_expands_broad_labor_topic_keywords():
    client = BpsClient("key", "1306")
    mock_empty_content_searches(client, "ketenagakerjaan")
    tpt_rows = [
        {"var_id": 301, "title": "Tingkat Pengangguran Terbuka (TPT) Laki-Laki Menurut Tingkat Pendidikan yang Ditamatkan", "unit": "Persen"},
        {"var_id": 302, "title": "Tingkat Partisipasi Angkatan Kerja (TPAK) Perempuan Menurut Pendidikan yang Ditamatkan", "unit": "Persen"},
    ]
    for keyword in [
        "ketenagakerjaan",
        "tenaga kerja",
        "angkatan kerja",
        "bekerja",
        "pengangguran",
        "tpt",
        "tpak",
        "jam kerja",
        "lapangan pekerjaan",
        "pendidikan yang ditamatkan",
    ]:
        mock_keyword_variable_search(keyword, tpt_rows if keyword in {"tpt", "tpak", "pengangguran", "angkatan kerja"} else [])
    for page in range(1, 6):
        respx.get(f"https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/{page}/area/1/key/key").mock(
            return_value=Response(200, json={"data-availability": "available", "data": [0, []]})
        )

    result = await client.search_variable_options("ketenagakerjaan")

    assert result.found
    assert "Tingkat Pengangguran Terbuka" in result.summary
    assert "Tingkat Partisipasi Angkatan Kerja" in result.summary
    assert "[Tabel Dinamis] Tingkat Pengangguran Terbuka" in result.summary


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_uses_ai_keyword_candidates_when_direct_keyword_is_weak():
    client = BpsClient("key", "1306")
    mock_empty_content_searches(client, "kerjaan", ["tpt"])
    tpt_rows = [{"var_id": 501, "title": "Tingkat Pengangguran Terbuka (TPT) Menurut Jenis Kelamin", "unit": "Persen"}]
    mock_keyword_variable_search("kerjaan")
    mock_keyword_variable_search("tpt", tpt_rows)
    for page in range(1, 6):
        respx.get(f"https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/{page}/area/1/key/key").mock(
            return_value=Response(200, json={"data-availability": "available", "data": [0, []]})
        )

    result = await client.search_variable_options("kerjaan", keywords=["tpt"])

    assert result.found
    assert "Tingkat Pengangguran Terbuka" in result.summary
    assert "kerjaan" in result.metadata["keywords"]
    assert "tpt" in result.metadata["keywords"]


def test_bps_client_learned_choice_boosts_previous_user_selection():
    client = BpsClient("key", "1306")
    matches = [
        {"var_id": 1, "title": "Jumlah Penduduk", "score": 0.9},
        {"var_id": 2, "title": "Kepadatan Penduduk", "score": 0.8},
    ]

    boosted = client._apply_learned_boost("penduduk", matches, {"penduduk": {"var_id": 2, "title": "Kepadatan Penduduk"}})

    assert boosted[0]["var_id"] == 2


def test_bps_client_topic_expansion_keywords_cover_common_broad_topics():
    client = BpsClient("key", "1306")

    labor = client._bps_keyword_queries("data ketenagakerjaan")
    economy = client._bps_keyword_queries("data ekonomi")
    health = client._bps_keyword_queries("data kesehatan")

    assert "tpt" in labor
    assert "tpak" in labor
    assert "produk domestik regional bruto" in economy
    assert "puskesmas" in health


def test_bps_client_short_keyword_score_requires_token_match():
    client = BpsClient("key", "1306")

    assert client._score("pdrb", "Jumlah Penduduk") == 0
    assert client._score("pdrb", "Produk Domestik Regional Bruto (PDRB) Atas Dasar Harga Berlaku") > 0
    assert client._score("ipm", "Jumlah Penduduk") == 0
    assert client._score("ipm", "Indeks Pembangunan Manusia (IPM)") > 0
    assert client._score("ketenagakerjaan", "Tingkat Pengangguran Terbuka (TPT) Laki-Laki") > 0


def test_bps_client_rows_to_matrix_does_not_truncate_rows_columns_or_text():
    client = BpsClient("key", "1306")
    long_text = "teks panjang " * 40
    rows = [
        {f"kolom_{column}": f"{long_text}{row}-{column}" for column in range(1, 8)}
        for row in range(1, 26)
    ]

    matrix = client._rows_to_matrix(rows)

    assert len(matrix) == 26
    assert len(matrix[0]) == 7
    assert matrix[-1][-1].endswith("25-7")
    assert "..." not in "\n".join("|".join(row) for row in matrix)


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_caches_bps_list_responses():
    client = BpsClient("key", "1306", cache_ttl_seconds=3600)
    route = respx.get("https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/1/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [1, [{"var_id": 1, "title": "Jumlah Penduduk"}]]})
    )

    first = await client._bps_list("1306", "var", page=1)
    second = await client._bps_list("1306", "var", page=1)

    assert first == second
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_cache_can_be_disabled():
    client = BpsClient("key", "1306", cache_ttl_seconds=0)
    route = respx.get("https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/1/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [0, []]})
    )

    await client._bps_list("1306", "var", page=1)
    await client._bps_list("1306", "var", page=1)

    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_persists_cache_to_sqlite(tmp_path):
    cache_db = tmp_path / "bps_cache.sqlite3"
    first_client = BpsClient("key", "1306", cache_ttl_seconds=3600, cache_db_path=str(cache_db))
    route = respx.get("https://webapi.bps.go.id/v1/api/list/model/var/lang/ind/domain/1306/page/1/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [1, [{"var_id": 1, "title": "Jumlah Penduduk"}]]})
    )

    first = await first_client._bps_list("1306", "var", page=1)
    second_client = BpsClient("key", "1306", cache_ttl_seconds=3600, cache_db_path=str(cache_db))
    second = await second_client._bps_list("1306", "var", page=1)

    assert first == second
    assert route.call_count == 1
    assert cache_db.exists()


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_uses_pending_candidate_for_followup_with_typo():
    client = BpsClient("key", "1306")
    candidate = {
        "var_id": 282,
        "title": "Jumlah Penduduk Laki-laki + Perempuan Berumur 15 Tahun Keatas Yang Bekerja Menurut Tingkat Pendidikan Yang Ditamatkan ",
        "unit": "Jiwa",
    }
    respx.get("https://webapi.bps.go.id/v1/api/list/model/th/lang/ind/domain/1306/var/282/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [1, [{"val": 123, "label": "2023"}]]})
    )
    respx.get("https://webapi.bps.go.id/v1/api/list/model/turth/lang/ind/domain/1306/var/282/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [1, [{"val": 0, "label": "Tahunan"}]]})
    )
    respx.get("https://webapi.bps.go.id/v1/api/list/model/vervar/lang/ind/domain/1306/var/282/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [0, []]})
    )
    respx.get("https://webapi.bps.go.id/v1/api/list/model/turvar/lang/ind/domain/1306/var/282/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [1, [{"val": 0, "label": "Total"}]]})
    )
    respx.get("https://webapi.bps.go.id/v1/api/list/model/data/lang/ind/domain/1306/var/282/th/123/turth/0/vervar/0/turvar/0/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [{"tahun": [{"val": 123, "label": "2023"}]}, {"datacontent": {"282123000": 12345}}]})
    )

    result = await client.search_data(
        "jumlah penduduk laki-laki+perempuan berumhr 15 tahun keatas yang bekerja menurut tingkat pendidikan yang ditamatkan",
        candidate_matches=[candidate],
    )

    assert result.found
    assert "2023" in result.summary
    assert "12345" in result.summary


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_uses_pending_candidate_number_selection():
    client = BpsClient("key", "1306")
    candidates = [
        {"var_id": 111, "title": "Pilihan Pertama", "unit": "Jiwa"},
        {"var_id": 282, "title": "Jumlah Penduduk Laki-laki + Perempuan Berumur 15 Tahun Keatas", "unit": "Jiwa"},
    ]
    respx.get("https://webapi.bps.go.id/v1/api/list/model/th/lang/ind/domain/1306/var/282/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [1, [{"val": 123, "label": "2023"}]]})
    )
    respx.get("https://webapi.bps.go.id/v1/api/list/model/turth/lang/ind/domain/1306/var/282/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [1, [{"val": 0, "label": "Tahunan"}]]})
    )
    respx.get("https://webapi.bps.go.id/v1/api/list/model/vervar/lang/ind/domain/1306/var/282/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [0, []]})
    )
    respx.get("https://webapi.bps.go.id/v1/api/list/model/turvar/lang/ind/domain/1306/var/282/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [1, [{"val": 0, "label": "Total"}]]})
    )
    respx.get("https://webapi.bps.go.id/v1/api/list/model/data/lang/ind/domain/1306/var/282/th/123/turth/0/vervar/0/turvar/0/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [{"tahun": [{"val": 123, "label": "2023"}]}, {"datacontent": {"282123000": 12345}}]})
    )

    result = await client.search_data("2", candidate_matches=candidates)

    assert result.found
    assert "[Tabel Dinamis]" in result.summary
    assert "Jumlah Penduduk" in result.summary
    assert "12345" in result.summary


@pytest.mark.asyncio
@respx.mock
async def test_bps_client_table_uses_vervar_labels_and_year_fallback_when_year_endpoint_500():
    client = BpsClient("key", "1306")
    variable = {
        "var_id": 279,
        "title": "Penduduk Perempuan Berumur 15 Tahun Ke Atas Yang Bekerja Menurut Jam Kerja Seminggu Yang Lalu",
        "unit": "Jiwa",
    }
    respx.get("https://webapi.bps.go.id/v1/api/list/model/th/lang/ind/domain/1306/var/279/key/key").mock(
        return_value=Response(500, json={"error": "server error"})
    )
    respx.get("https://webapi.bps.go.id/v1/api/list").mock(
        return_value=Response(500, json={"error": "server error"})
    )
    respx.get("https://webapi.bps.go.id/v1/api/list/model/turth/lang/ind/domain/1306/var/279/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [1, [{"turth_id": 0, "turth": "Tahunan"}]]})
    )
    respx.get("https://webapi.bps.go.id/v1/api/list/model/vervar/lang/ind/domain/1306/var/279/key/key").mock(
        return_value=Response(
            200,
            json={
                "data-availability": "available",
                "data": [
                    5,
                    [
                        {"kode_ver_id": 1, "vervar": "0"},
                        {"kode_ver_id": 2, "vervar": "1-14"},
                        {"kode_ver_id": 3, "vervar": "15-34"},
                        {"kode_ver_id": 4, "vervar": "35+"},
                        {"kode_ver_id": 5, "vervar": "Jumlah"},
                    ],
                ],
            },
        )
    )
    respx.get("https://webapi.bps.go.id/v1/api/list/model/turvar/lang/ind/domain/1306/var/279/key/key").mock(
        return_value=Response(200, json={"data-availability": "available", "data": [0, []]})
    )
    for vervar_id, value in [("1", 0), ("2", 10911), ("3", 21792), ("4", 48320), ("5", 81023)]:
        respx.get(f"https://webapi.bps.go.id/v1/api/list/model/data/lang/ind/domain/1306/var/279/th/123/turth/0/vervar/{vervar_id}/turvar/0/key/key").mock(
            return_value=Response(200, json={"data-availability": "available", "data": [{"datacontent": {"a": value}}]})
        )

    result = await client.fetch_table_by_variable("penduduk perempuan bekerja", variable, ["2023"])

    assert result.found
    assert "0       | 0" in result.message
    assert "1-14    | 10911" in result.message
    assert "15-34   | 21792" in result.message
    assert "35+     | 48320" in result.message
    assert "Jumlah  | 81023" in result.message
    assert "Rincian 1" not in result.message
