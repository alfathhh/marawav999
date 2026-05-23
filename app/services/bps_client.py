from dataclasses import dataclass
from copy import deepcopy
from difflib import SequenceMatcher
from html.parser import HTMLParser
import asyncio
import base64
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
import time
from typing import Any
from urllib.parse import quote

import httpx
from playwright.async_api import async_playwright, Browser


logger = logging.getLogger(__name__)

SOURCE_DYNAMIC_TABLE = "Tabel Dinamis"
SOURCE_SIMDASI = "SIMDASI"
SOURCE_PUBLICATION = "Publikasi"
DATA_OPTION_PREVIEW_SIZE = 5
BPS_HTTP_TIMEOUT = httpx.Timeout(8.0, connect=3.0, read=8.0, write=5.0, pool=3.0)


TOPIC_KEYWORD_EXPANSIONS = {
    "ketenagakerjaan": [
        "tenaga kerja",
        "angkatan kerja",
        "bekerja",
        "pengangguran",
        "tpt",
        "tpak",
        "jam kerja",
        "lapangan pekerjaan",
        "pendidikan yang ditamatkan",
    ],
    "tenaga kerja": [
        "ketenagakerjaan",
        "angkatan kerja",
        "bekerja",
        "pengangguran",
        "tpt",
        "tpak",
        "jam kerja",
    ],
    "ekonomi": [
        "pdrb",
        "produk domestik regional bruto",
        "pertumbuhan ekonomi",
        "laju pertumbuhan",
        "harga berlaku",
        "harga konstan",
        "pengeluaran",
        "konsumsi",
    ],
    "penduduk": [
        "jumlah penduduk",
        "kepadatan penduduk",
        "kelompok umur",
        "jenis kelamin",
        "rasio jenis kelamin",
        "penduduk usia sekolah",
        "penduduk dewasa",
    ],
    "kependudukan": [
        "jumlah penduduk",
        "kepadatan penduduk",
        "kelompok umur",
        "jenis kelamin",
        "rasio jenis kelamin",
    ],
    "pendidikan": [
        "sekolah",
        "murid",
        "guru",
        "angka partisipasi sekolah",
        "aps",
        "apm",
        "apk",
        "melek huruf",
        "ijazah",
        "pendidikan yang ditamatkan",
    ],
    "kesehatan": [
        "fasilitas kesehatan",
        "tenaga kesehatan",
        "rumah sakit",
        "puskesmas",
        "posyandu",
        "kelahiran",
        "kematian",
        "angka harapan hidup",
    ],
    "kemiskinan": [
        "penduduk miskin",
        "garis kemiskinan",
        "persentase penduduk miskin",
        "indeks kedalaman kemiskinan",
        "indeks keparahan kemiskinan",
        "gini rasio",
    ],
    "pertanian": [
        "tanaman pangan",
        "padi",
        "jagung",
        "hortikultura",
        "perkebunan",
        "peternakan",
        "perikanan",
        "luas panen",
        "produksi",
    ],
    "wilayah": [
        "luas wilayah",
        "kecamatan",
        "nagari",
        "desa",
        "jarak",
        "geografi",
        "administrasi",
    ],
    "perumahan": [
        "rumah tangga",
        "air minum",
        "sanitasi",
        "listrik",
        "lantai",
        "dinding",
        "atap",
    ],
    "pariwisata": [
        "hotel",
        "akomodasi",
        "wisatawan",
        "restoran",
        "rumah makan",
    ],
    "inflasi": [
        "indeks harga konsumen",
        "ihk",
        "harga konsumen",
        "kelompok pengeluaran",
    ],
    "publikasi": [
        "publikasi bps",
        "katalog bps",
        "dalam angka",
    ],
    "dalam angka": [
        "kabupaten padang pariaman dalam angka",
        "padang pariaman dalam angka",
        "daerah dalam angka",
    ],
    "potensi desa": [
        "statistik potensi desa",
        "podes",
        "publikasi potensi desa",
    ],
}

ACRONYM_EXPANSIONS = {
    "ipm": ["indeks pembangunan manusia", "pembangunan manusia"],
    "pdrb": ["produk domestik regional bruto", "pertumbuhan ekonomi", "harga berlaku", "harga konstan"],
    "tpt": ["tingkat pengangguran terbuka", "pengangguran"],
    "tpak": ["tingkat partisipasi angkatan kerja", "angkatan kerja"],
    "ihk": ["indeks harga konsumen", "harga konsumen", "inflasi"],
    "ahh": ["angka harapan hidup", "harapan hidup"],
    "rls": ["rata rata lama sekolah", "lama sekolah"],
    "hls": ["harapan lama sekolah"],
}


@dataclass
class BpsSearchResult:
    found: bool
    summary: str
    source_url: str | None = None
    metadata: dict[str, Any] | None = None
    too_many: bool = False


@dataclass
class BpsTableResult:
    found: bool
    message: str
    source_url: str | None = None
    metadata: dict[str, Any] | None = None


class HtmlTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._current_table = []
        elif tag == "tr" and self._current_table is not None:
            self._current_row = []
        elif tag in {"td", "th"} and self._current_row is not None:
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._current_cell is not None and self._current_row is not None:
            text = " ".join(" ".join(self._current_cell).split())
            self._current_row.append(text)
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None and self._current_table is not None:
            if any(cell for cell in self._current_row):
                self._current_table.append(self._current_row)
            self._current_row = None
        elif tag == "table" and self._current_table is not None:
            if self._current_table:
                self.tables.append(self._current_table)
            self._current_table = None


class BpsClient:
    def __init__(
        self,
        api_key: str,
        domain: str = "1306",
        base_url: str = "https://webapi.bps.go.id/v1/api",
        cache_ttl_seconds: int = 3600,
        cache_max_items: int = 512,
        cache_db_path: str = "",
    ):
        self.api_key = api_key
        self.domain = domain
        self.base_url = base_url.rstrip("/")
        self.cache_ttl_seconds = max(cache_ttl_seconds, 0)
        self.cache_max_items = max(cache_max_items, 0)
        self.cache_db_path = cache_db_path.strip()
        self._cache_namespace = hashlib.sha256(f"{self.base_url}:{self.api_key}".encode()).hexdigest()[:16]
        self._cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
        self._init_sqlite_cache()
        # Playwright / Cloudflare session state (lazy-initialized)
        self._pw_instance = None
        self._pw_browser: Browser | None = None
        self._cf_cookies: dict[str, str] = {}
        self._cf_cookies_ts: float = 0.0
        self._cf_refresh_lock: asyncio.Lock = asyncio.Lock()
        # Hanya aktifkan Playwright jika base_url mengarah langsung ke BPS
        # (bukan ke proxy yang sudah handle Cloudflare sendiri)
        self._use_playwright = "webapi.bps.go.id" in self.base_url

    # --- Playwright / Cloudflare cookie management ---

    _CF_COOKIE_TTL = 25 * 60   # 25 menit (cf_clearance valid ~30 menit)
    _CF_BROWSER_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://webapi.bps.go.id/",
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }

    async def _ensure_browser(self) -> None:
        if self._pw_browser is None:
            logger.info("bps.playwright.starting_browser")
            self._pw_instance = await async_playwright().start()
            self._pw_browser = await self._pw_instance.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            logger.info("bps.playwright.browser_ready")

    async def _solve_cf_challenge(self, url: str) -> dict[str, str]:
        await self._ensure_browser()
        logger.info("bps.playwright.solving_cf_challenge url=%s", url)
        context = await self._pw_browser.new_context(
            user_agent=self._CF_BROWSER_HEADERS["User-Agent"],
            locale="id-ID",
            timezone_id="Asia/Jakarta",
            viewport={"width": 1280, "height": 800},
        )
        try:
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            for _ in range(30):
                title = await page.title()
                if "Just a moment" not in title and "Attention Required" not in title:
                    break
                await asyncio.sleep(1)
            cookies = await context.cookies()
            result = {c["name"]: c["value"] for c in cookies}
            logger.info("bps.playwright.cf_solved cookies=%s", list(result.keys()))
            return result
        finally:
            await context.close()

    async def _get_cf_cookies(self, url: str, force: bool = False) -> dict[str, str]:
        if not self._use_playwright:
            return {}
        async with self._cf_refresh_lock:
            age = time.monotonic() - self._cf_cookies_ts
            if force or not self._cf_cookies or age > self._CF_COOKIE_TTL:
                self._cf_cookies = await self._solve_cf_challenge(url)
                self._cf_cookies_ts = time.monotonic()
        return self._cf_cookies

    def _is_cf_challenge_response(self, response: httpx.Response) -> bool:
        if response.status_code != 403:
            return False
        ct = response.headers.get("content-type", "")
        if "text/html" not in ct:
            return False
        preview = response.text[:512]
        return "Just a moment" in preview or "cf_chl" in preview or "Attention Required" in preview

    async def _http_get(self, url: str) -> httpx.Response:
        """
        Buat GET request ke BPS. Kalau base_url mengarah ke webapi.bps.go.id
        (bukan proxy), inject Cloudflare cookies via Playwright secara otomatis.
        Retry sekali kalau dapat CF challenge.
        """
        for attempt in range(2):
            cookies = await self._get_cf_cookies(url, force=attempt > 0)
            headers = self._CF_BROWSER_HEADERS if self._use_playwright else {}
            async with httpx.AsyncClient(
                timeout=BPS_HTTP_TIMEOUT,
                follow_redirects=True,
                headers=headers,
                cookies=cookies,
            ) as client:
                response = await client.get(url)
            if self._use_playwright and self._is_cf_challenge_response(response):
                logger.warning("bps.playwright.cf_challenge_on_attempt attempt=%d url=%s", attempt + 1, self._safe_url(url))
                if attempt == 0:
                    continue
            return response
        return response  # fallback, seharusnya tidak sampai sini

    async def search_data(
        self,
        query: str,
        domain: str | None = None,
        candidate_matches: list[dict[str, Any]] | None = None,
        keywords: list[str] | None = None,
    ) -> BpsSearchResult:
        domain = domain or self.domain
        logger.info("bps.search.start query=%r domain=%s", query, domain)
        if not self.api_key:
            logger.warning("bps.search.missing_api_key query=%r domain=%s", query, domain)
            return BpsSearchResult(False, "BPS_API_KEY belum dikonfigurasi.", metadata={"reason": "missing_api_key"})
        try:
            matches = self._match_pending_candidates(query, candidate_matches or [])
            if matches:
                logger.info(
                    "bps.search.using_pending_candidates query=%r top=%s",
                    query,
                    [{"var_id": item.get("var_id"), "title": item.get("title"), "score": round(float(item.get("score", 0)), 3)} for item in matches[:3]],
                )
            else:
                matches = await self._search_variables(query, domain, keywords=keywords, expand_topics=False)
            if not matches:
                logger.info("bps.search.no_variable_matches query=%r domain=%s", query, domain)
                return await self._content_fallback(query, domain)

            if self._is_ambiguous(query, matches):
                logger.info(
                    "bps.search.ambiguous query=%r matches=%s",
                    query,
                    [{"var_id": item.get("var_id"), "title": item.get("title"), "score": round(float(item.get("score", 0)), 3)} for item in matches[:5]],
                )
                names = "\n".join(f"{index}. {self._option_title(item)}" for index, item in enumerate(matches[:5], start=1))
                return BpsSearchResult(
                    True,
                    "Saya menemukan beberapa data yang mirip.\n"
                    "Mana yang paling sesuai dengan kebutuhan Anda?\n\n"
                    f"{names}\n\n"
                    "Silakan ketik nomor pilihannya.\n"
                    "Jika belum ada yang sesuai, tuliskan kata kunci yang lebih detail beserta tahun yang dibutuhkan.",
                    metadata={"query": query, "matches": matches[:5]},
                    too_many=True,
                )

            selected = matches[0]
            logger.info(
                "bps.search.selected_variable query=%r var_id=%s title=%r score=%.3f",
                query,
                selected.get("var_id"),
                selected.get("title"),
                float(selected.get("score", 0)),
            )
            return await self.fetch_data_by_variable(query, selected, domain)
        except httpx.HTTPStatusError as exc:
            logger.exception(
                "bps.search.http_status_error query=%r status_code=%s url=%s",
                query,
                exc.response.status_code,
                self._safe_url(str(exc.request.url)),
            )
            return BpsSearchResult(False, "BPS WebAPI sedang tidak bisa diakses atau key tidak valid.", metadata={"error": str(exc)})
        except httpx.HTTPError as exc:
            logger.exception("bps.search.http_error query=%r error=%s", query, exc)
            return BpsSearchResult(False, "Koneksi ke BPS WebAPI sedang bermasalah.", metadata={"error": str(exc)})

    async def search_variable_options(
        self,
        query: str,
        domain: str | None = None,
        limit: int = 20,
        keywords: list[str] | None = None,
        learned_choices: dict[str, Any] | None = None,
    ) -> BpsSearchResult:
        domain = domain or self.domain
        source_errors: dict[str, Any] = {}
        started = time.perf_counter()

        async def dynamic_search() -> list[dict[str, Any]]:
            dynamic_started = time.perf_counter()
            try:
                matches = await self._search_variables(query, domain, keywords=keywords)
                return self._apply_learned_boost(query, matches, learned_choices or {})
            finally:
                logger.info("bps.timing query=%r bps_dynamic_ms=%.1f", query, self._elapsed_ms(dynamic_started))

        async def simdasi_search() -> BpsSearchResult:
            simdasi_started = time.perf_counter()
            try:
                return await self.search_simdasi_options(query, domain=domain, keywords=keywords, limit=limit)
            finally:
                logger.info("bps.timing query=%r bps_simdasi_ms=%.1f", query, self._elapsed_ms(simdasi_started))

        async def publication_search() -> BpsSearchResult:
            publication_started = time.perf_counter()
            try:
                return await self.search_publication_options(query, domain=domain, keywords=keywords, limit=limit)
            finally:
                logger.info("bps.timing query=%r publication_ms=%.1f", query, self._elapsed_ms(publication_started))

        dynamic_result, simdasi_result, publication_result = await asyncio.gather(
            dynamic_search(),
            simdasi_search(),
            publication_search(),
            return_exceptions=True,
        )
        local_groups = self._local_source_groups(query, domain, keywords, limit)
        if isinstance(dynamic_result, Exception):
            logger.error("bps.options.error query=%r error=%s", query, dynamic_result)
            dynamic_matches = local_groups["dynamic_table"]
            source_errors["error"] = str(dynamic_result)
        else:
            dynamic_matches = self._merge_indexed_items(dynamic_result, local_groups["dynamic_table"])

        if isinstance(simdasi_result, Exception):
            logger.error("bps.simdasi.options_unhandled query=%r error=%s", query, simdasi_result)
            source_errors["simdasi_error"] = str(simdasi_result)
            simdasi_result = BpsSearchResult(False, "Data SIMDASI belum ditemukan.", metadata={})
        if isinstance(publication_result, Exception):
            logger.error("bps.publication.options_unhandled query=%r error=%s", query, publication_result)
            source_errors["publication_error"] = str(publication_result)
            publication_result = BpsSearchResult(False, "Data publikasi belum ditemukan.", metadata={})
        if self._has_source_error(simdasi_result.metadata):
            source_errors.update(simdasi_result.metadata or {})
        if self._has_source_error(publication_result.metadata):
            source_errors.update(publication_result.metadata or {})

        simdasi_matches = self._merge_indexed_items(
            simdasi_result.metadata.get("matches", []) if simdasi_result.metadata else [],
            local_groups["simdasi"],
        )
        publication_matches = self._merge_indexed_items(
            publication_result.metadata.get("matches", []) if publication_result.metadata else [],
            local_groups["publication"],
        )
        source_groups = {
            "dynamic_table": dynamic_matches[:limit],
            "simdasi": simdasi_matches[:limit],
            "publication": publication_matches[:limit],
        }
        matches = [*source_groups["dynamic_table"], *source_groups["simdasi"], *source_groups["publication"]]
        if not matches:
            if source_errors and "error" in source_errors:
                return BpsSearchResult(
                    False,
                    "Maaf, BPS WebAPI sedang bermasalah saat mencari data.\n\nSilakan coba lagi beberapa saat lagi.",
                    metadata={"query": query, **source_errors},
                )
            return BpsSearchResult(False, "Data belum ditemukan.", metadata={"query": query, "keywords": self._candidate_queries(query, keywords), "source_groups": source_groups})
        summary = self._format_grouped_options_summary(source_groups)
        return BpsSearchResult(
            True,
            summary,
            metadata={
                "query": query,
                "keywords": self._candidate_queries(query, keywords),
                "matches": matches[:limit * 3],
                "source_groups": source_groups,
                "bps_ms": self._elapsed_ms(started),
            },
            too_many=True,
        )

    async def search_simdasi_options(
        self,
        query: str,
        domain: str | None = None,
        keywords: list[str] | None = None,
        limit: int = 20,
    ) -> BpsSearchResult:
        domain = domain or self.domain
        try:
            matches = await self._search_static_tables(query, domain, keywords=keywords)
        except httpx.HTTPStatusError as exc:
            logger.warning("bps.simdasi.options_status_error query=%r status_code=%s", query, exc.response.status_code)
            return BpsSearchResult(
                False,
                "Maaf, BPS WebAPI sedang bermasalah saat mencari data SIMDASI.\n\nSilakan coba lagi beberapa saat lagi.",
                metadata={"simdasi_error": str(exc), "query": query},
            )
        except httpx.HTTPError as exc:
            logger.warning("bps.simdasi.options_error query=%r error=%s", query, exc)
            return BpsSearchResult(
                False,
                "Maaf, BPS WebAPI sedang bermasalah saat mencari data SIMDASI.\n\nSilakan coba lagi beberapa saat lagi.",
                metadata={"simdasi_error": str(exc), "query": query},
            )
        if not matches:
            return BpsSearchResult(False, "Data SIMDASI belum ditemukan.", metadata={"query": query, "keywords": self._candidate_queries(query, keywords)})
        names = "\n".join(f"{index}. {self._option_title(item)}" for index, item in enumerate(matches[:5], start=1))
        return BpsSearchResult(
            True,
            "Saya menemukan beberapa tabel SIMDASI yang mirip.\n"
            "Mana yang paling sesuai dengan kebutuhan Anda?\n\n"
            f"{names}\n\n"
            "Silakan ketik nomor pilihannya.\n"
            "Jika belum ada yang sesuai, tuliskan kata kunci yang lebih detail.",
            metadata={"query": query, "keywords": self._candidate_queries(query, keywords), "matches": matches[:limit]},
            too_many=True,
        )

    async def search_publication_options(
        self,
        query: str,
        domain: str | None = None,
        keywords: list[str] | None = None,
        limit: int = 20,
    ) -> BpsSearchResult:
        domain = domain or self.domain
        try:
            matches = await self._search_publications(query, domain, keywords=keywords)
        except httpx.HTTPStatusError as exc:
            logger.warning("bps.publication.options_status_error query=%r status_code=%s", query, exc.response.status_code)
            return BpsSearchResult(
                False,
                "Maaf, BPS WebAPI sedang bermasalah saat mencari publikasi.\n\nSilakan coba lagi beberapa saat lagi.",
                metadata={"publication_error": str(exc), "query": query},
            )
        except httpx.HTTPError as exc:
            logger.warning("bps.publication.options_error query=%r error=%s", query, exc)
            return BpsSearchResult(
                False,
                "Maaf, BPS WebAPI sedang bermasalah saat mencari publikasi.\n\nSilakan coba lagi beberapa saat lagi.",
                metadata={"publication_error": str(exc), "query": query},
            )
        if not matches:
            return BpsSearchResult(False, "Data belum ditemukan.", metadata={"query": query, "keywords": self._candidate_queries(query, keywords)})
        names = "\n".join(f"{index}. {self._option_title(item)}" for index, item in enumerate(matches[:5], start=1))
        return BpsSearchResult(
            True,
            "Saya menemukan beberapa publikasi yang mirip.\n"
            "Mana yang paling sesuai dengan kebutuhan Anda?\n\n"
            f"{names}\n\n"
            "Silakan ketik nomor pilihannya.\n"
            "Jika belum ada yang sesuai, tuliskan kata kunci yang lebih detail.",
            metadata={"query": query, "keywords": self._candidate_queries(query, keywords), "matches": matches[:limit]},
            too_many=True,
        )

    def _format_grouped_options_summary(self, source_groups: dict[str, list[dict[str, Any]]]) -> str:
        from app.services.wa_formatter import format_data_options, ICON_CHECK
        source_pages = {key: 0 for key in ("dynamic_table", "simdasi", "publication")}
        options_message, _, _ = format_data_options(
            source_groups=source_groups,
            source_pages=source_pages,
            page_size=DATA_OPTION_PREVIEW_SIZE,
        )
        return options_message

    async def fetch_table_by_variable(
        self,
        query: str,
        variable: dict[str, Any],
        years: list[str],
        periods: list[str] | None = None,
        domain: str | None = None,
    ) -> BpsTableResult:
        domain = domain or self.domain
        if self._is_simdasi_candidate(variable):
            return await self._simdasi_table_result(variable, years, domain)
        if self._is_publication_candidate(variable):
            return self._publication_table_result(variable, years, domain)
        var_id = str(variable.get("var_id") or variable.get("id") or variable.get("var") or "")
        if not var_id:
            return BpsTableResult(False, "Variabel BPS tidak valid.", metadata={"query": query, "variable": variable})
        try:
            year_items = await self._dimension_rows(domain, "th", var_id, years)
            turth_items = await self._dimension_rows(domain, "turth", var_id, years)
            vervar_items = await self._dimension_rows(domain, "vervar", var_id, years)
            turvar_items = await self._dimension_rows(domain, "turvar", var_id, years)
            selected_years = self._select_year_items(year_items, years)
            selected_year_labels = [self._item_label(item) for item in selected_years if self._item_label(item)]
            missing_years = [year for year in years if year not in selected_year_labels]
            selected_periods = self._select_period_items(turth_items, periods or [])
            if not selected_years:
                available_years = [self._item_label(item) for item in year_items if self._item_label(item)]
                available = ", ".join(available_years[:12])
                return BpsTableResult(
                    False,
                    "Maaf, tahun yang diminta belum tersedia untuk data ini.\n\n"
                    f"Tahun yang tersedia: {available or '-'}",
                    metadata={
                        "reason": "year_unavailable",
                        "query": query,
                        "variable": variable,
                        "requested_years": years,
                        "available_years": available_years,
                    },
                )
            row_dimension, row_items = self._choose_row_dimension(vervar_items, turvar_items, turth_items)
            if selected_periods:
                row_dimension, row_items = self._choose_row_dimension(vervar_items, turvar_items, [])
            turth = None if row_dimension == "turth" else self._pick_total_or_first(turth_items)
            vervar = None if row_dimension == "vervar" else self._pick_region_or_first(vervar_items, query)
            turvar = None if row_dimension == "turvar" else self._pick_total_or_first(turvar_items)
            rows = self._table_row_labels(row_items)
            table: dict[str, list[str]] = {}
            source_url = None
            for year in selected_years:
                period_items = selected_periods or [turth]
                for period in period_items:
                    values: list[str] = []
                    column_label = self._period_column_label(year, period if selected_periods else None)
                    if row_dimension and row_items:
                        for row_item in row_items:
                            params = self._table_params(var_id, year, period, vervar, turvar, row_dimension, row_item)
                            data = await self._fetch_variable_data(domain, params)
                            row_values = self._collect_values(data)
                            values.append(row_values[0] if row_values else "-")
                            if source_url is None:
                                source_url = self._source_url(domain, params)
                    else:
                        params = self._table_params(var_id, year, period, vervar, turvar)
                        data = await self._fetch_variable_data(domain, params)
                        values = self._collect_values(data)
                        if source_url is None:
                            source_url = self._source_url(domain, params)
                    table[column_label] = values
            title = self._source_title(SOURCE_DYNAMIC_TABLE, str(variable.get("title") or query).strip())
            unit = variable.get("unit") or variable.get("satuan") or "-"
            message = self._format_table_message(title, unit, rows, table, missing_years)
            return BpsTableResult(
                True,
                message,
                source_url=source_url,
                metadata={
                    "query": query,
                    "variable": variable,
                    "requested_years": years,
                    "displayed_years": selected_year_labels,
                    "missing_years": missing_years,
                    "periods": periods or [],
                },
            )
        except httpx.HTTPError as exc:
            logger.exception("bps.table.error query=%r var_id=%s error=%s", query, var_id, exc)
            return BpsTableResult(
                False,
                "Maaf, koneksi ke BPS WebAPI sedang bermasalah.\n\nSilakan coba lagi beberapa saat lagi.",
                metadata={"error": str(exc), "query": query, "variable": variable},
            )

    async def get_period_options(self, variable: dict[str, Any], domain: str | None = None) -> list[dict[str, Any]]:
        domain = domain or self.domain
        if self._is_publication_candidate(variable) or self._is_simdasi_candidate(variable):
            return []
        var_id = str(variable.get("var_id") or variable.get("id") or variable.get("var") or "")
        if not var_id:
            return []
        return await self._dimension_rows(domain, "turth", var_id, [])

    async def _dimension_rows(self, domain: str, model: str, var_id: str, requested_years: list[str]) -> list[dict[str, Any]]:
        try:
            first_page = await self._bps_list(domain, model, var=var_id)
            rows = self._rows(first_page)
            # Check if there are more pages (BPS API default page size is 10)
            if isinstance(first_page, dict) and isinstance(first_page.get("data"), list):
                data = first_page["data"]
                # data[0] is typically the total count of items
                total = data[0] if data and isinstance(data[0], int) else 0
                page_size = 10
                if total > page_size:
                    total_pages = (total + page_size - 1) // page_size
                    for page_num in range(2, total_pages + 1):
                        next_page = await self._bps_list(domain, model, var=var_id, page=page_num)
                        rows.extend(self._rows(next_page))
            return rows
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "bps.dimension.unavailable model=%s var_id=%s status_code=%s url=%s",
                model,
                var_id,
                exc.response.status_code,
                self._safe_url(str(exc.request.url)),
            )
            if model == "th":
                return self._year_fallback_items(requested_years)
            return []

    def _year_fallback_items(self, years: list[str]) -> list[dict[str, Any]]:
        items = []
        for year in years:
            if year.isdigit() and len(year) == 4:
                items.append({"th_id": str(int(year) - 1900), "th": year})
        return items

    def has_quarterly_periods(self, periods: list[dict[str, Any]]) -> bool:
        return any("triwulan" in self._item_label(item).lower() for item in periods)

    def _table_params(
        self,
        var_id: str,
        year: dict[str, Any],
        turth: dict[str, Any] | None,
        vervar: dict[str, Any] | None,
        turvar: dict[str, Any] | None,
        row_dimension: str | None = None,
        row_item: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        params = {
            "var": var_id,
            "th": self._item_id(year),
            "turth": self._item_id(turth) if turth else "0",
            "vervar": self._item_id(vervar) if vervar else "0",
            "turvar": self._item_id(turvar) if turvar else "0",
        }
        if row_dimension and row_item:
            params[row_dimension] = self._item_id(row_item)
        return self._clean_params(params)

    async def fetch_data_by_variable(self, query: str, variable: dict[str, Any], domain: str | None = None) -> BpsSearchResult:
        domain = domain or self.domain
        var_id = str(variable.get("var_id") or variable.get("id") or variable.get("var") or "")
        if not var_id:
            return BpsSearchResult(False, "Variabel BPS tidak valid.", metadata={"query": query, "variable": variable})
        params = await self._resolve_dynamic_params(domain, var_id, query)
        if not params:
            logger.info("bps.search.no_dynamic_params query=%r var_id=%s", query, var_id)
            return await self._content_fallback(query, domain, {"variable": variable})
        data = await self._fetch_variable_data(domain, params)
        values = self._collect_values(data)
        if not values:
            logger.info("bps.search.empty_dynamic_data query=%r params=%s raw_keys=%s", query, params, list(data.keys()))
            return await self._content_fallback(query, domain, {"variable": variable, "params": params})
        logger.info("bps.search.dynamic_found query=%r params=%s value_count=%d", query, params, len(values))
        return self._summarize(query, variable, params, data, domain)

    async def _bps_list(self, domain: str, model: str, **params: Any) -> dict[str, Any]:
        cache_key = self._cache_key(domain, model, params)
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("bps.cache.hit model=%s domain=%s params=%s", model, domain, params)
            return cached
        path_params = "".join(f"/{key}/{quote(str(value))}" for key, value in params.items() if value is not None)
        url = f"{self.base_url}/list/model/{model}/lang/ind/domain/{domain}{path_params}/key/{self.api_key}"
        safe_url = self._safe_url(url)
        logger.debug("bps.request model=%s url=%s", model, safe_url)
        response = await self._http_get(url)
        if response.status_code >= 500:
            query_url = f"{self.base_url}/list"
            query_params = {"model": model, "lang": "ind", "domain": domain, "key": self.api_key, **params}
            logger.warning(
                "bps.request.path_failed_retry_query model=%s status_code=%s url=%s",
                model,
                response.status_code,
                safe_url,
            )
            async with httpx.AsyncClient(timeout=BPS_HTTP_TIMEOUT, cookies=self._cf_cookies, headers=self._CF_BROWSER_HEADERS if self._use_playwright else {}) as client:
                response = await client.get(query_url, params=query_params)
        if response.status_code == 403:
            logger.warning(
                "bps.request.forbidden model=%s url=%s — API key mungkin expired atau tidak valid",
                model,
                safe_url,
            )
            return {}
        response.raise_for_status()
        payload = response.json()
        self._cache_set(cache_key, payload)
        logger.debug(
            "bps.response model=%s availability=%s row_count=%d",
            model,
            payload.get("data-availability") if isinstance(payload, dict) else None,
            len(self._rows(payload)),
        )
        return payload

    async def _bps_static_table_list(self, domain: str, keyword: str = "", page: int = 1) -> dict[str, Any]:
        params = {"keyword": keyword, "page": page, "perpage": 100000}
        cache_key = self._cache_key(domain, "statictable:list", params)
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("bps.cache.hit model=statictable domain=%s keyword=%r", domain, keyword)
            return cached
        url = (
            f"{self.base_url}/list/model/statictable/perpage/100000/lang/ind/domain/{quote(domain)}"
            f"/key/{self.api_key}/keyword/{quote(keyword)}/page/{page}"
        )
        safe_url = self._safe_url(url)
        logger.debug("bps.simdasi.request url=%s", safe_url)
        response = await self._http_get(url)
        if response.status_code == 403:
            logger.warning("bps.simdasi.forbidden url=%s", safe_url)
            return {}
        response.raise_for_status()
        payload = response.json()
        self._cache_set(cache_key, payload)
        logger.debug(
            "bps.simdasi.response availability=%s row_count=%d",
            payload.get("data-availability") if isinstance(payload, dict) else None,
            len(self._rows(payload)),
        )
        return payload

    async def _search_variables(
        self,
        keyword: str,
        domain: str,
        max_page: int = 5,
        expand_topics: bool = True,
        keywords: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        cache_key = self._cache_key(
            domain,
            "search:variables",
            {"keyword": keyword, "keywords": "|".join(keywords or []), "max_page": max_page, "expand_topics": expand_topics},
        )
        cached = self._cache_get(cache_key)
        if cached is not None:
            return list(cached.get("matches", []))
        queries = self._candidate_queries(keyword, keywords, include_expansions=expand_topics)
        results: list[dict[str, Any]] = []
        last_error: httpx.HTTPError | None = None
        for query in queries[:12]:
            try:
                results.extend(await self._search_variables_by_keyword(keyword, domain, [query]))
            except httpx.HTTPError as exc:
                logger.warning("bps.var.keyword_unavailable query=%r keyword=%r error=%s", keyword, query, exc)
                last_error = exc
                continue
            if results:
                break

        try:
            index_rows = await self._variable_index(domain, max_page=max_page)
        except httpx.HTTPError as exc:
            logger.warning("bps.var.index_unavailable query=%r error=%s", keyword, exc)
            index_rows = []
            last_error = exc
        for item in index_rows:
            normalized = self._normalize_variable_candidate(item, keyword, queries)
            if normalized:
                results.append(normalized)

        results = self._dedupe_variables(results)
        if not results and last_error:
            raise last_error
        logger.info(
            "bps.var.matches query=%r count=%d top=%s",
            keyword,
            len(results),
            [{"var_id": item.get("var_id"), "title": item.get("title"), "score": round(float(item.get("score", 0)), 3)} for item in sorted(results, key=lambda item: item["score"], reverse=True)[:5]],
        )
        matches = sorted(results, key=lambda item: item["score"], reverse=True)[:30]
        self._index_items(domain, "dynamic_table", matches)
        self._cache_set(cache_key, {"matches": matches})
        return matches

    async def _search_variables_by_keyword(self, keyword: str, domain: str, queries: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for bps_keyword in queries:
            try:
                payload = await self._bps_list_with_optional_area(domain, "var", page=1, keyword=bps_keyword)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500:
                    logger.warning("bps.var.keyword_skip_server_error query=%r keyword=%r", keyword, bps_keyword)
                    continue
                raise
            except httpx.HTTPError as exc:
                logger.warning("bps.var.keyword_stop_http_error query=%r keyword=%r error=%s", keyword, bps_keyword, exc)
                break
            rows = self._rows(payload)
            logger.info("bps.var.keyword query=%r keyword=%r rows=%d", keyword, bps_keyword, len(rows))
            for item in rows:
                normalized = self._normalize_variable_candidate(item, keyword, [bps_keyword])
                if normalized:
                    results.append(normalized)
        return self._dedupe_variables(results)

    async def _search_publications(
        self,
        keyword: str,
        domain: str,
        keywords: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        cache_key = self._cache_key(domain, "search:publication", {"keyword": keyword, "keywords": "|".join(keywords or [])})
        cached = self._cache_get(cache_key)
        if cached is not None:
            return list(cached.get("matches", []))
        queries = self._candidate_queries(keyword, keywords)
        results: list[dict[str, Any]] = []
        last_error: httpx.HTTPError | None = None
        for query in queries[:8]:
            try:
                payload = await self._bps_list(domain, "publication", keyword=query)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500:
                    logger.warning(
                        "bps.publication.keyword_skip_server_error query=%r keyword=%r status_code=%s url=%s",
                        keyword,
                        query,
                        exc.response.status_code,
                        self._safe_url(str(exc.request.url)),
                    )
                    last_error = exc
                    continue
                raise
            except httpx.HTTPError as exc:
                logger.warning("bps.publication.keyword_stop_http_error query=%r keyword=%r error=%s", keyword, query, exc)
                last_error = exc
                break
            rows = self._rows(payload)
            logger.info("bps.publication.keyword query=%r keyword=%r rows=%d", keyword, query, len(rows))
            for item in rows:
                normalized = self._normalize_publication_candidate(item, keyword, queries, domain)
                if normalized:
                    results.append(normalized)
        results = self._dedupe_publications(results)
        if not results and last_error:
            raise last_error
        matches = sorted(results, key=lambda item: item["score"], reverse=True)
        self._index_items(domain, "publication", matches)
        self._cache_set(cache_key, {"matches": matches})
        return matches

    async def _search_static_tables(
        self,
        keyword: str,
        domain: str,
        keywords: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        cache_key = self._cache_key(domain, "search:statictable", {"keyword": keyword, "keywords": "|".join(keywords or [])})
        cached = self._cache_get(cache_key)
        if cached is not None:
            return list(cached.get("matches", []))
        queries = self._candidate_queries(keyword, keywords)
        results: list[dict[str, Any]] = []
        last_error: httpx.HTTPError | None = None
        for query in queries[:8]:
            try:
                payload = await self._bps_static_table_list(domain, keyword=query)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500:
                    logger.warning(
                        "bps.simdasi.keyword_skip_server_error query=%r keyword=%r status_code=%s url=%s",
                        keyword,
                        query,
                        exc.response.status_code,
                        self._safe_url(str(exc.request.url)),
                    )
                    last_error = exc
                    continue
                raise
            except httpx.HTTPError as exc:
                logger.warning("bps.simdasi.keyword_stop_http_error query=%r keyword=%r error=%s", keyword, query, exc)
                last_error = exc
                break
            rows = self._rows(payload)
            logger.info("bps.simdasi.keyword query=%r keyword=%r rows=%d", keyword, query, len(rows))
            for item in rows:
                normalized = self._normalize_static_table_candidate(item, keyword, queries, domain)
                if normalized:
                    results.append(normalized)
            if results:
                break
        results = self._dedupe_static_tables(results)
        if not results and last_error:
            raise last_error
        matches = sorted(results, key=lambda item: item["score"], reverse=True)
        self._index_items(domain, "simdasi", matches)
        self._cache_set(cache_key, {"matches": matches})
        return matches

    def _normalize_static_table_candidate(
        self,
        item: dict[str, Any],
        query: str,
        candidate_queries: list[str],
        domain: str,
    ) -> dict[str, Any] | None:
        title = self._static_table_title(item)
        if not title:
            return None
        score_candidates = [self._publication_score(query, title)]
        score_candidates.extend(self._publication_score(candidate, title) * 0.95 for candidate in candidate_queries)
        score = max(score_candidates)
        if score <= 0:
            return None
        normalized = dict(item)
        normalized["title"] = title
        normalized["score"] = score
        normalized["source_type"] = "simdasi"
        normalized["source_label"] = SOURCE_SIMDASI
        normalized["source_url"] = self._static_table_source_url(domain, normalized)
        normalized["release_date"] = normalized.get("rl_date") or normalized.get("release_date") or normalized.get("tanggal_rilis") or normalized.get("updated_at") or "-"
        return normalized

    def _dedupe_static_tables(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: dict[str, dict[str, Any]] = {}
        for item in items:
            key = str(self._static_table_id(item) or item.get("source_url") or item.get("title") or "").strip()
            if not key:
                continue
            current = deduped.get(key)
            if current is None or float(item.get("score", 0)) > float(current.get("score", 0)):
                deduped[key] = item
        return list(deduped.values())

    def _normalize_publication_candidate(
        self,
        item: dict[str, Any],
        query: str,
        candidate_queries: list[str],
        domain: str,
    ) -> dict[str, Any] | None:
        title = str(item.get("title") or item.get("judul") or item.get("label") or item.get("name") or "")
        if not title:
            return None
        score_candidates = [self._publication_score(query, title)]
        score_candidates.extend(self._publication_score(candidate, title) * 0.95 for candidate in candidate_queries)
        score = max(score_candidates)
        if score <= 0:
            return None
        normalized = dict(item)
        normalized["title"] = title
        normalized["score"] = score
        normalized["source_type"] = "publication"
        normalized["source_label"] = SOURCE_PUBLICATION
        normalized["source_url"] = self._publication_source_url(domain, normalized)
        normalized["release_date"] = normalized.get("rl_date") or normalized.get("release_date") or normalized.get("tanggal_rilis") or "-"
        return normalized

    def _publication_score(self, query: str, title: str) -> float:
        query_norm = self._normalize_text(query)
        title_norm = self._normalize_text(title)
        tokens = self._meaningful_tokens(query_norm)
        if not tokens:
            return self._score(query, title)
        matched = sum(1 for token in tokens if token in title_norm)
        if matched == 0:
            return 0
        score = matched / len(tokens)
        if query_norm in title_norm:
            score += 0.3
        return max(score, 0)

    def _dedupe_publications(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: dict[str, dict[str, Any]] = {}
        for item in items:
            key = str(self._publication_id(item) or item.get("source_url") or item.get("title") or "").strip()
            if not key:
                continue
            current = deduped.get(key)
            if current is None or float(item.get("score", 0)) > float(current.get("score", 0)):
                deduped[key] = item
        return list(deduped.values())

    def _bps_keyword_queries(self, keyword: str, include_expansions: bool = True) -> list[str]:
        return self._candidate_queries(keyword, include_expansions=include_expansions)

    def _candidate_queries(
        self,
        keyword: str,
        keywords: list[str] | None = None,
        include_expansions: bool = True,
    ) -> list[str]:
        normalized = self._normalize_text(keyword)
        queries = [keyword.strip(), *(keywords or [])]
        if include_expansions:
            for acronym, expansions in ACRONYM_EXPANSIONS.items():
                if acronym in self._meaningful_tokens(normalized) or any(word in normalized for word in expansions):
                    queries.extend(expansions)
            queries.extend(self._topic_expansions(normalized))
        cleaned = [self._normalize_text(item) for item in queries if item and self._normalize_text(item)]
        return [item for index, item in enumerate(cleaned) if item not in cleaned[:index]]

    def _topic_expansions(self, normalized_keyword: str) -> list[str]:
        expansions: list[str] = []
        for trigger, keywords in TOPIC_KEYWORD_EXPANSIONS.items():
            if trigger in normalized_keyword:
                expansions.extend(keywords)
        return expansions

    async def _variable_index(self, domain: str, max_page: int = 5) -> list[dict[str, Any]]:
        cache_key = (domain, "var-index", max_page)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return list(cached.get("rows", []))
        rows: list[dict[str, Any]] = []
        for page in range(1, max_page + 1):
            payload = await self._bps_list_with_optional_area(domain, "var", page=page)
            page_rows = self._rows(payload)
            logger.info("bps.var.index_page page=%d rows=%d", page, len(page_rows))
            rows.extend(page_rows)
        indexed_rows = [item for item in (self._normalize_variable_candidate(row, "", []) for row in rows) if item]
        self._index_items(domain, "dynamic_table", indexed_rows)
        self._cache_set(cache_key, {"rows": rows})
        return rows

    def _normalize_variable_candidate(
        self,
        item: dict[str, Any],
        query: str,
        candidate_queries: list[str] | None = None,
    ) -> dict[str, Any] | None:
        title = str(item.get("title") or item.get("label") or item.get("name") or "")
        var_id = item.get("var_id") or item.get("id") or item.get("var")
        if not title or not var_id:
            return None
        score_candidates = [self._score(query, title)]
        score_candidates.extend(self._score(candidate, title) * 0.95 for candidate in candidate_queries or [])
        score = max(score_candidates)
        if score <= 0:
            return None
        normalized = dict(item)
        normalized["title"] = title
        normalized["var_id"] = var_id
        normalized["score"] = score
        normalized["source_type"] = "dynamic_table"
        normalized["source_label"] = SOURCE_DYNAMIC_TABLE
        return normalized

    def _apply_learned_boost(
        self,
        query: str,
        matches: list[dict[str, Any]],
        learned_choices: dict[str, Any],
    ) -> list[dict[str, Any]]:
        selected = learned_choices.get(self._normalize_text(query))
        if not selected:
            return matches
        selected_var_id = str(selected.get("var_id") or selected.get("id") or selected.get("var") or "")
        boosted = []
        seen = set()
        for item in matches:
            copy = dict(item)
            var_id = str(copy.get("var_id") or copy.get("id") or copy.get("var") or "")
            if var_id == selected_var_id:
                copy["score"] = max(float(copy.get("score", 0)), 1.5)
            boosted.append(copy)
            seen.add(var_id)
        if selected_var_id and selected_var_id not in seen:
            copy = dict(selected)
            copy["var_id"] = selected_var_id
            copy["score"] = 1.4
            boosted.insert(0, copy)
        return sorted(boosted, key=lambda item: item.get("score", 0), reverse=True)

    def _dedupe_variables(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: dict[str, dict[str, Any]] = {}
        for item in items:
            var_id = str(item.get("var_id") or item.get("id") or item.get("var") or "")
            if not var_id:
                continue
            current = deduped.get(var_id)
            if current is None or float(item.get("score", 0)) > float(current.get("score", 0)):
                deduped[var_id] = item
        return list(deduped.values())

    def _local_source_groups(
        self,
        query: str,
        domain: str,
        keywords: list[str] | None,
        limit: int,
    ) -> dict[str, list[dict[str, Any]]]:
        return {
            "dynamic_table": self._local_index_search(domain, "dynamic_table", query, keywords, limit),
            "simdasi": self._local_index_search(domain, "simdasi", query, keywords, limit),
            "publication": self._local_index_search(domain, "publication", query, keywords, limit),
        }

    def _local_index_search(
        self,
        domain: str,
        source_type: str,
        query: str,
        keywords: list[str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not self.cache_db_path:
            return []
        query_candidates = self._candidate_queries(query, keywords)
        try:
            with sqlite3.connect(self.cache_db_path) as connection:
                rows = connection.execute(
                    "SELECT payload FROM bps_index WHERE namespace = ? AND domain = ? AND source_type = ?",
                    (self._cache_namespace, domain, source_type),
                ).fetchall()
        except sqlite3.Error as exc:
            logger.warning("bps.index.local_search_failed source=%s error=%s", source_type, exc)
            return []

        matches: list[dict[str, Any]] = []
        for (payload_json,) in rows:
            try:
                item = json.loads(payload_json)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(item, dict):
                continue
            title = self._indexed_title(item)
            if not title:
                continue
            if source_type == "dynamic_table":
                score = max(self._score(candidate, title) for candidate in query_candidates)
            else:
                score = max(self._publication_score(candidate, title) for candidate in query_candidates)
            if score <= 0:
                continue
            item = dict(item)
            if "score" in item:
                item["indexed_score"] = item.get("score")
            item["score"] = score
            matches.append(item)

        deduped = self._dedupe_indexed_items(matches)
        logger.info("bps.index.local source=%s query=%r count=%d", source_type, query, len(deduped))
        return sorted(deduped, key=lambda item: item["score"], reverse=True)[:limit]

    def _merge_indexed_items(self, primary: list[dict[str, Any]], fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(self._dedupe_indexed_items([*primary, *fallback]), key=lambda item: item["score"], reverse=True)

    def _dedupe_indexed_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: dict[str, dict[str, Any]] = {}
        for item in items:
            key = self._index_item_key(item)
            if not key:
                continue
            current = deduped.get(key)
            if current is None or float(item.get("score", 0) or 0) > float(current.get("score", 0) or 0):
                deduped[key] = item
        return list(deduped.values())

    def _index_items(self, domain: str, source_type: str, items: list[dict[str, Any]]) -> None:
        if not self.cache_db_path or not items:
            return
        records: list[tuple[str, str, str, str, str, str, float]] = []
        for item in items:
            key = self._index_item_key(item)
            title = self._indexed_title(item)
            if not key or not title:
                continue
            payload = dict(item)
            payload["source_type"] = source_type
            if source_type == "dynamic_table":
                payload.setdefault("source_label", SOURCE_DYNAMIC_TABLE)
            elif source_type == "simdasi":
                payload.setdefault("source_label", SOURCE_SIMDASI)
            elif source_type == "publication":
                payload.setdefault("source_label", SOURCE_PUBLICATION)
            try:
                payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError):
                continue
            records.append(
                (
                    self._cache_namespace,
                    domain,
                    source_type,
                    key,
                    self._normalize_text(title),
                    payload_json,
                    time.time(),
                )
            )
        if not records:
            return
        try:
            with sqlite3.connect(self.cache_db_path) as connection:
                connection.executemany(
                    "INSERT OR REPLACE INTO bps_index("
                    "namespace, domain, source_type, item_key, title_norm, payload, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    records,
                )
            logger.info("bps.index.updated source=%s count=%d", source_type, len(records))
        except sqlite3.Error as exc:
            logger.warning("bps.index.update_failed source=%s error=%s", source_type, exc)

    def _index_item_key(self, item: dict[str, Any]) -> str:
        source_type = str(item.get("source_type") or "").lower()
        if source_type == "dynamic_table" or item.get("var_id") or item.get("var"):
            value = item.get("var_id") or item.get("id") or item.get("var")
            return f"dynamic_table:{value}" if value not in (None, "") else ""
        if source_type == "simdasi":
            value = self._static_table_id(item) or item.get("source_url") or self._indexed_title(item)
            return f"simdasi:{value}" if value else ""
        if source_type == "publication":
            value = self._publication_id(item) or item.get("source_url") or self._indexed_title(item)
            return f"publication:{value}" if value else ""
        title = self._indexed_title(item)
        return f"unknown:{title}" if title else ""

    def _indexed_title(self, item: dict[str, Any]) -> str:
        return str(item.get("title") or item.get("label") or item.get("name") or item.get("judul") or "").strip()

    async def _bps_list_with_optional_area(self, domain: str, model: str, **params: Any) -> dict[str, Any]:
        try:
            return await self._bps_list(domain, model, **params, area=1)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500 and exc.response.status_code != 403:
                raise
            logger.warning(
                "bps.request.area_failed_retry_without_area model=%s status_code=%s params=%s",
                model,
                exc.response.status_code,
                params,
            )
            try:
                return await self._bps_list(domain, model, **params)
            except httpx.HTTPStatusError as second_exc:
                if second_exc.response.status_code >= 500 and params.get("page") != 1:
                    logger.warning(
                        "bps.request.page_failed_skip model=%s status_code=%s params=%s",
                        model,
                        second_exc.response.status_code,
                        params,
                    )
                    return {"data-availability": "available", "data": [0, []]}
                raise

    async def _resolve_dynamic_params(self, domain: str, var_id: str, query: str) -> dict[str, str] | None:
        years = self._rows(await self._bps_list(domain, "th", var=var_id))
        turth_items = self._rows(await self._bps_list(domain, "turth", var=var_id))
        vervar_items = self._rows(await self._bps_list(domain, "vervar", var=var_id))
        turvar_items = self._rows(await self._bps_list(domain, "turvar", var=var_id))
        logger.info(
            "bps.params.rows query=%r var_id=%s th=%d turth=%d vervar=%d turvar=%d",
            query,
            var_id,
            len(years),
            len(turth_items),
            len(vervar_items),
            len(turvar_items),
        )

        year = self._pick_latest_year(years)
        turth = self._pick_total_or_first(turth_items)
        vervar = self._pick_region_or_first(vervar_items, query)
        turvar = self._pick_total_or_first(turvar_items)

        if not year:
            return None
        params = self._clean_params({
            "var": var_id,
            "th": self._item_id(year),
            "turth": self._item_id(turth) if turth else "0",
            "vervar": self._item_id(vervar) if vervar else "0",
            "turvar": self._item_id(turvar) if turvar else "0",
        })
        logger.info("bps.params.resolved query=%r var_id=%s params=%s", query, var_id, params)
        return params

    async def _fetch_variable_data(self, domain: str, params: dict[str, str]) -> dict[str, Any]:
        return await self._bps_list(domain, "data", **params)

    async def _bps_view(self, domain: str, model: str, item_id: str) -> dict[str, Any]:
        cache_key = self._cache_key(domain, f"view:{model}", {"id": item_id})
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("bps.cache.hit view model=%s domain=%s id=%s", model, domain, item_id)
            return cached
        url = f"{self.base_url}/view/domain/{quote(domain)}/model/{model}/lang/ind/id/{quote(item_id)}/key/{self.api_key}"
        safe_url = self._safe_url(url)
        logger.debug("bps.view_request model=%s url=%s", model, safe_url)
        response = await self._http_get(url)
        if response.status_code >= 500:
            query_url = f"{self.base_url}/view"
            query_params = {"domain": domain, "model": model, "lang": "ind", "id": item_id, "key": self.api_key}
            logger.warning(
                "bps.view.path_failed_retry_query model=%s status_code=%s url=%s",
                model,
                response.status_code,
                safe_url,
            )
            async with httpx.AsyncClient(timeout=BPS_HTTP_TIMEOUT, cookies=self._cf_cookies, headers=self._CF_BROWSER_HEADERS if self._use_playwright else {}) as client:
                response = await client.get(query_url, params=query_params)
        if response.status_code == 403:
            logger.warning("bps.view.forbidden model=%s url=%s", model, safe_url)
            return {}
        response.raise_for_status()
        payload = response.json()
        self._cache_set(cache_key, payload)
        return payload

    async def _content_fallback(
        self,
        query: str,
        domain: str,
        metadata: dict[str, Any] | None = None,
    ) -> BpsSearchResult:
        simdasi = await self._simdasi_fallback(query, domain, metadata)
        if simdasi.found:
            return simdasi
        merged_metadata = {**(metadata or {}), **(simdasi.metadata or {})}
        return await self._publication_fallback(query, domain, merged_metadata)

    async def _content_fallback_for_queries(self, query: str, domain: str, keywords: list[str]) -> BpsSearchResult:
        tried = self._candidate_queries(query, keywords)
        last_result: BpsSearchResult | None = None
        for candidate in tried[:8]:
            result = await self._content_fallback(candidate, domain, {"original_query": query, "tried_keywords": tried})
            if result.found:
                return result
            last_result = result
        if last_result and self._has_source_error(last_result.metadata):
            return BpsSearchResult(
                False,
                "Maaf, BPS WebAPI sedang bermasalah saat mencari data.\n\nSilakan coba lagi beberapa saat lagi.",
                metadata=last_result.metadata,
            )
        return last_result or BpsSearchResult(False, "Data belum ditemukan.", metadata={"query": query, "tried_keywords": tried})

    async def _simdasi_fallback(
        self,
        query: str,
        domain: str,
        metadata: dict[str, Any] | None = None,
    ) -> BpsSearchResult:
        try:
            matches = await self._search_static_tables(query, domain)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                logger.warning(
                    "bps.simdasi.unavailable query=%r status_code=%s url=%s",
                    query,
                    exc.response.status_code,
                    self._safe_url(str(exc.request.url)),
                )
                return BpsSearchResult(
                    False,
                    "Maaf, BPS WebAPI sedang bermasalah saat mencari data SIMDASI.\n\nSilakan coba lagi beberapa saat lagi.",
                    metadata={"query": query, "simdasi_error": str(exc), **(metadata or {})},
                )
            raise
        except httpx.HTTPError as exc:
            logger.warning("bps.simdasi.unavailable query=%r error=%s", query, exc)
            return BpsSearchResult(
                False,
                "Maaf, BPS WebAPI sedang bermasalah saat mencari data SIMDASI.\n\nSilakan coba lagi beberapa saat lagi.",
                metadata={"query": query, "simdasi_error": str(exc), **(metadata or {})},
            )
        if not matches:
            logger.info("bps.simdasi.not_found query=%r domain=%s metadata_keys=%s", query, domain, list((metadata or {}).keys()))
            return BpsSearchResult(False, "Data SIMDASI belum ditemukan.", metadata={"query": query, **(metadata or {})})

        selected = matches[0]
        logger.info("bps.simdasi.found query=%r title=%r", query, self._static_table_title(selected))
        from app.services.wa_formatter import ICON_TABLE, ICON_CALENDAR
        title = self._static_table_title(selected) or query
        release = selected.get("rl_date") or selected.get("release_date") or selected.get("tanggal_rilis") or selected.get("updated_at") or "-"
        source_url = self._static_table_source_url(domain, selected)
        view_payload = await self._static_table_view_payload(domain, selected)
        table = self._extract_table_matrix(view_payload or selected)
        if table:
            summary = self._format_matrix_message(
                f"[{SOURCE_SIMDASI}] {title}",
                table,
                intro="Saya belum menemukan data yang sesuai di tabel dinamis, tetapi ada tabel SIMDASI yang terkait.",
                source="BPS Kab. Padang Pariaman via SIMDASI WebAPI.",
                note=f"Tanggal/metadata rilis: {release}" if release != "-" else "",
            )
        else:
            summary = (
                "Saya belum menemukan data yang sesuai di tabel dinamis, tetapi ada tabel SIMDASI yang terkait.\n\n"
                f"{ICON_TABLE} *[{SOURCE_SIMDASI}] {title}*\n"
                f"{ICON_CALENDAR} Tanggal/metadata rilis: {release}\n\n"
                "_Isi tabel SIMDASI belum tersedia dalam format yang bisa dibaca otomatis oleh bot._\n\n"
                "_Sumber: BPS Kab. Padang Pariaman via SIMDASI WebAPI._"
            )
        return BpsSearchResult(
            True,
            summary,
            source_url=source_url,
            metadata={"query": query, "simdasi": selected, "simdasi_view": view_payload, **(metadata or {})},
        )

    async def _publication_fallback(
        self,
        query: str,
        domain: str,
        metadata: dict[str, Any] | None = None,
    ) -> BpsSearchResult:
        try:
            payload = await self._bps_list(domain, "publication", keyword=query)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                logger.warning(
                    "bps.publication.unavailable query=%r status_code=%s url=%s",
                    query,
                    exc.response.status_code,
                    self._safe_url(str(exc.request.url)),
                )
                return BpsSearchResult(
                    False,
                    "Maaf, BPS WebAPI sedang bermasalah saat mencari publikasi.\n\nSilakan coba lagi beberapa saat lagi.",
                    metadata={"query": query, "publication_error": str(exc), **(metadata or {})},
                )
            raise
        publications = self._rows(payload)
        if not publications:
            logger.info("bps.publication.not_found query=%r domain=%s metadata_keys=%s", query, domain, list((metadata or {}).keys()))
            return BpsSearchResult(False, "Data belum ditemukan.", metadata={"query": query, **(metadata or {})})

        selected = publications[0]
        logger.info("bps.publication.found query=%r title=%r", query, selected.get("title") or selected.get("judul"))
        from app.services.wa_formatter import ICON_BOOK, ICON_CALENDAR, ICON_LINK, ICON_PIN
        title = selected.get("title") or selected.get("judul") or query
        release = selected.get("rl_date") or selected.get("release_date") or selected.get("tanggal_rilis") or "-"
        source_url = self._publication_source_url(domain, selected)
        abstract = selected.get("abstract") or selected.get("abstrak") or selected.get("description") or selected.get("deskripsi") or ""
        abstract_text = f"\n\n{ICON_PIN} *Ringkasan:*\n{str(abstract).strip()}" if abstract else ""
        link_text = f"\n\n{ICON_LINK} {source_url}" if source_url else ""
        summary = (
            "Saya belum menemukan data yang sesuai di tabel dinamis, tetapi ada publikasi BPS yang terkait.\n\n"
            f"{ICON_BOOK} *{title}*\n"
            f"{ICON_CALENDAR} Tanggal rilis: {release}"
            f"{abstract_text}"
            f"{link_text}\n\n"
            "_Sumber: BPS Kab. Padang Pariaman via WebAPI_"
        )
        return BpsSearchResult(True, summary, source_url=source_url, metadata={"query": query, "publication": selected, **(metadata or {})})

    async def _simdasi_table_result(self, table_item: dict[str, Any], years: list[str], domain: str) -> BpsTableResult:
        from app.services.wa_formatter import ICON_TABLE, ICON_CALENDAR, ICON_PIN
        title = self._source_title(SOURCE_SIMDASI, self._static_table_title(table_item) or table_item.get("title") or "Tabel SIMDASI")
        release = table_item.get("rl_date") or table_item.get("release_date") or table_item.get("tanggal_rilis") or table_item.get("updated_at") or "-"
        source_url = table_item.get("source_url") or self._static_table_source_url(domain, table_item)
        view_payload = await self._static_table_view_payload(domain, table_item)
        table = self._extract_table_matrix(view_payload or table_item)
        note = f"Tahun diminta: {', '.join(years)}" if years else ""
        if release != "-":
            note = f"{note}\nTanggal/metadata rilis: {release}" if note else f"Tanggal/metadata rilis: {release}"
        if table:
            message = self._format_matrix_message(
                title,
                table,
                intro="Berikut tabel SIMDASI yang dipilih.",
                source="BPS Kabupaten Padang Pariaman via SIMDASI WebAPI.",
                note=note,
            )
        else:
            year_text = f"{ICON_CALENDAR} Tahun diminta: {', '.join(years)}\n" if years else ""
            message = (
                f"{ICON_TABLE} *{title}*\n\n"
                f"{year_text}"
                f"{ICON_CALENDAR} Tanggal/metadata rilis: {release}\n\n"
                f"{ICON_PIN} _Isi tabel SIMDASI belum tersedia dalam format yang bisa dibaca otomatis oleh bot._\n\n"
                "_Sumber: BPS Kab. Padang Pariaman via SIMDASI WebAPI._"
            )
        release_year = str(release)[:4] if re.match(r"\d{4}", str(release)) else ""
        return BpsTableResult(
            True,
            message,
            source_url=source_url,
            metadata={
                "query": table_item.get("title") or title,
                "simdasi": table_item,
                "simdasi_view": view_payload,
                "requested_years": years,
                "displayed_years": [release_year] if release_year else years,
                "missing_years": [],
            },
        )

    def _publication_table_result(self, publication: dict[str, Any], years: list[str], domain: str) -> BpsTableResult:
        from app.services.wa_formatter import format_publication
        requested_years = years or []
        release = str(publication.get("rl_date") or publication.get("release_date") or publication.get("tanggal_rilis") or publication.get("release_date") or "-")
        title = str(publication.get("title") or publication.get("judul") or "Publikasi BPS").strip()
        source_url = publication.get("source_url") or self._publication_source_url(domain, publication)
        abstract = publication.get("abstract") or publication.get("abstrak") or publication.get("description") or publication.get("deskripsi") or ""
        year_note = f"Tahun diminta: {', '.join(requested_years)}" if requested_years else ""
        message = format_publication(
            title=title,
            release_date=release,
            abstract=str(abstract).strip(),
            source_url=source_url,
            year_note=year_note,
        )
        release_year = release[:4] if re.match(r"\d{4}", release) else ""
        missing_years = [year for year in requested_years if release_year and year != release_year]
        return BpsTableResult(
            True,
            message,
            source_url=source_url,
            metadata={
                "query": title,
                "publication": publication,
                "requested_years": requested_years,
                "displayed_years": [release_year] if release_year else requested_years,
                "missing_years": missing_years,
            },
        )

    def _is_publication_candidate(self, item: dict[str, Any]) -> bool:
        return str(item.get("source_type") or "").lower() == "publication"

    def _is_simdasi_candidate(self, item: dict[str, Any]) -> bool:
        return str(item.get("source_type") or "").lower() == "simdasi"

    def _has_source_error(self, metadata: dict[str, Any] | None) -> bool:
        if not metadata:
            return False
        return any(key in metadata for key in ("error", "simdasi_error", "publication_error"))

    def _prefers_content_source(self, query: str, keywords: list[str] | None = None) -> bool:
        normalized = self._normalize_text(" ".join([query, *(keywords or [])]))
        return any(
            phrase in normalized
            for phrase in (
                "publikasi",
                "dalam angka",
                "katalog",
                "pdf",
                "buku",
                "potensi desa",
                "podes",
            )
        )

    def _publication_source_url(self, domain: str, item: dict[str, Any]) -> str | None:
        for key in ("url", "link", "web_url", "publication_url", "halaman_url"):
            value = item.get(key)
            if value and "download.php" not in str(value).lower() and ".pdf" not in str(value).lower():
                return str(value)
        detail_url = self._publication_detail_url(domain, item)
        if detail_url:
            return detail_url
        return self._publication_listing_url(domain)

    def _publication_listing_url(self, domain: str) -> str:
        host_by_domain = {"1306": "padangpariamankab.bps.go.id"}
        host = host_by_domain.get(domain, "www.bps.go.id")
        return f"https://{host}/id/publication"

    def _publication_detail_url(self, domain: str, item: dict[str, Any]) -> str | None:
        publication_id = self._publication_id(item)
        title = str(item.get("title") or item.get("judul") or "").strip()
        release = str(item.get("rl_date") or item.get("release_date") or item.get("tanggal_rilis") or "").strip()
        if not publication_id or not title or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", release):
            return None
        year, month, day = release.split("-")
        slug = self._slug(title)
        return f"{self._publication_listing_url(domain)}/{year}/{month}/{day}/{publication_id}/{slug}.html"

    def _publication_id(self, item: dict[str, Any]) -> str:
        for key in ("id", "pub_id", "publication_id", "kode", "id_pub", "pubID", "publicationID"):
            value = item.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    def _slug(self, text: str) -> str:
        normalized = self._normalize_text(text)
        normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
        return normalized.strip("-")

    async def _static_table_view_payload(self, domain: str, item: dict[str, Any]) -> dict[str, Any] | None:
        table_id = self._static_table_id(item)
        if not table_id:
            return None
        try:
            return await self._bps_static_table_view(domain, table_id)
        except httpx.HTTPError as exc:
            logger.warning("bps.simdasi.view_unavailable id=%s error=%s", table_id, exc)
            return None

    async def _bps_static_table_view(self, domain: str, table_id: str) -> dict[str, Any]:
        cache_key = self._cache_key(domain, "statictable:view", {"id": table_id})
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("bps.cache.hit view model=statictable domain=%s id=%s", domain, table_id)
            return cached
        url = f"{self.base_url}/view/model/statictable/lang/ind/domain/{quote(domain)}/id/{quote(table_id)}/key/{self.api_key}/"
        safe_url = self._safe_url(url)
        logger.debug("bps.simdasi.view_request url=%s", safe_url)
        response = await self._http_get(url)
        response.raise_for_status()
        payload = response.json()
        self._cache_set(cache_key, payload)
        return payload

    def _static_table_title(self, item: dict[str, Any]) -> str:
        for key in ("title", "judul", "label", "name", "table_name", "nama_tabel"):
            value = item.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    def _static_table_source_url(self, domain: str, item: dict[str, Any]) -> str | None:
        for key in ("url", "link", "download_url", "table_url"):
            value = item.get(key)
            if value:
                return str(value)
        table_id = self._static_table_id(item)
        title = self._static_table_title(item)
        if not table_id or not title:
            return None
        encoded_id = quote(base64.b64encode(f"{table_id}#1".encode()).decode(), safe="")
        return f"{self._statistics_table_listing_url(domain)}/1/{encoded_id}/{self._slug(title)}.html"

    def _static_table_id(self, item: dict[str, Any]) -> str:
        for key in ("table_id", "id", "subject_id", "kode", "id_tabel", "tableID"):
            value = item.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    def _statistics_table_listing_url(self, domain: str) -> str:
        host_by_domain = {"1306": "padangpariamankab.bps.go.id"}
        host = host_by_domain.get(domain, "www.bps.go.id")
        return f"https://{host}/id/statistics-table"

    def _summarize(self, query: str, variable: dict[str, Any], params: dict[str, str], data: dict[str, Any], domain: str) -> BpsSearchResult:
        from app.services.wa_formatter import ICON_TABLE, ICON_CALENDAR, ICON_DOT
        title = variable.get("title") or variable.get("label") or variable.get("name") or query
        unit = variable.get("unit") or variable.get("satuan") or self._find_first(data, ("unit", "satuan")) or "-"
        years = self._collect_years(data)
        latest_values = self._collect_values(data)
        source_url = self._source_url(domain, params)
        year_text = ", ".join(years) if years else "tahun tersedia di WebAPI"
        if len(latest_values) > 1 and self._has_datacontent(data):
            summary = (
                f"{ICON_TABLE} *{title}*\n\n"
                f"{ICON_CALENDAR} Tahun: {year_text}\n"
                f"_Satuan: {unit}_\n\n"
                "Namun WebAPI mengembalikan beberapa angka rincian tanpa label kategori yang cukup jelas untuk ditampilkan sebagai jawaban final.\n"
                "Mohon perjelas kategori yang dibutuhkan, misalnya menurut jenis kelamin, kelompok umur, kecamatan, atau tahun tertentu.\n\n"
                "_Sumber: BPS Kab. Padang Pariaman via WebAPI_"
            )
            return BpsSearchResult(True, summary, source_url=source_url, metadata={"query": query, "variable": variable, "params": params, "raw_keys": list(data.keys()), "needs_clarification": True}, too_many=True)
        value_text = "\n".join(f"{ICON_DOT} {item}" for item in latest_values) if latest_values else f"{ICON_DOT} Detail nilai tersedia melalui BPS WebAPI."
        summary = (
            f"{ICON_TABLE} *{title}*\n\n"
            f"{ICON_CALENDAR} Tahun: {year_text}\n"
            f"_Satuan: {unit}_\n\n"
            f"{value_text}\n\n"
            f"_Sumber: BPS Kab. Padang Pariaman via WebAPI_"
        )
        return BpsSearchResult(True, summary, source_url=source_url, metadata={"query": query, "variable": variable, "params": params, "raw_keys": list(data.keys())})

    def _option_title(self, item: dict[str, Any]) -> str:
        title = str(item.get("title") or item.get("label") or item.get("name") or "-").strip()
        source_label = str(item.get("source_label") or SOURCE_DYNAMIC_TABLE).strip()
        return self._source_title(source_label, title)

    @staticmethod
    def _source_title(source_label: str, title: Any) -> str:
        clean_title = str(title or "-").strip()
        clean_label = str(source_label or "").strip()
        if not clean_label or clean_title.startswith("["):
            return clean_title
        return f"[{clean_label}] {clean_title}"

    def _score(self, query: str, title: str) -> float:
        query_norm = self._normalize_text(query)
        title_lower = self._normalize_text(title)
        tokens = self._meaningful_tokens(self._expanded_query_text(query_norm))
        direct_tokens = self._meaningful_tokens(query_norm)
        matched = sum(1 for token in tokens if token in title_lower)
        direct_matched = sum(1 for token in direct_tokens if token in title_lower)
        if direct_tokens and not direct_matched and len(query_norm) <= 8:
            return 0
        if tokens and not matched:
            return 0
        token_score = matched / max(len(tokens), 1)
        similarity = SequenceMatcher(None, query_norm, title_lower).ratio() if len(query_norm) > 8 else 0
        score = max(token_score, similarity)
        if query_norm in title_lower:
            score += 0.2
        if "pdrb" in query_norm and ("pdrb" in title_lower or "produk domestik regional bruto" in title_lower):
            score += 0.3
        for acronym, expansions in ACRONYM_EXPANSIONS.items():
            if acronym in direct_tokens and (acronym in title_lower or any(expansion in title_lower for expansion in expansions)):
                score += 0.35
        if "padang pariaman" in title_lower:
            score += 0.1
        return score

    def _expanded_query_text(self, query: str) -> str:
        topic_words = " ".join(self._topic_expansions(query))
        acronym_words = []
        query_tokens = self._meaningful_tokens(query)
        for acronym, expansions in ACRONYM_EXPANSIONS.items():
            if acronym in query_tokens:
                acronym_words.extend(expansions)
        if "kerjaan" in query or "kerja" in query:
            acronym_words.extend(["ketenagakerjaan", "tenaga kerja", "angkatan kerja", "bekerja"])
        if "pengangguran" in query:
            acronym_words.extend(["tingkat pengangguran terbuka", "tpt"])
        return f"{query} {' '.join(acronym_words)} {topic_words}".strip()

    def _match_pending_candidates(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        selected_number = self._selected_candidate_number(query)
        if selected_number is not None:
            index = selected_number - 1
            if 0 <= index < len(candidates):
                item = dict(candidates[index])
                item["score"] = 1.0
                return [item]
            return []
        ranked: list[dict[str, Any]] = []
        for candidate in candidates:
            title = str(candidate.get("title") or candidate.get("label") or candidate.get("name") or "")
            if not title:
                continue
            score = self._score(query, title)
            if score >= 0.55:
                item = dict(candidate)
                item["score"] = score
                ranked.append(item)
        return sorted(ranked, key=lambda item: item["score"], reverse=True)

    def _selected_candidate_number(self, query: str) -> int | None:
        normalized = self._normalize_text(query)
        if normalized.isdigit():
            return int(normalized)
        match = re.fullmatch(r"(nomor|no|pilih|opsi)\s+(\d+)", normalized)
        if match:
            return int(match.group(2))
        return None

    def pick_candidate(self, query: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        matches = self._match_pending_candidates(query, candidates)
        return matches[0] if matches else None

    def _is_ambiguous(self, query: str | list[dict[str, Any]], matches: list[dict[str, Any]] | None = None) -> bool:
        if matches is None:
            matches = query  # type: ignore[assignment]
            query = ""
        query_text = str(query)
        if len(self._meaningful_tokens(query_text)) <= 1 and len(matches) > 1:
            return True
        if len(matches) < 5:
            return False
        best = float(matches[0].get("score", 0))
        fifth = float(matches[4].get("score", 0))
        return best < 0.75 and fifth >= best - 0.1

    def _meaningful_tokens(self, query: str) -> set[str]:
        stopwords = {
            "data",
            "jumlah",
            "berapa",
            "minta",
            "cari",
            "carikan",
            "terbaru",
            "tahun",
            "kabupaten",
            "padang",
            "pariaman",
        }
        return {token for token in self._normalize_text(query).split() if len(token) > 2 and token not in stopwords and not token.isdigit()}

    def _normalize_text(self, text: str) -> str:
        normalized = text.lower().replace("+", " ")
        replacements = {
            "berumhr": "berumur",
            "keatas": "ke atas",
            "laki laki": "laki-laki",
        }
        for old, new in replacements.items():
            normalized = normalized.replace(old, new)
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    def _rows(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict) and payload.get("data-availability") == "not-available":
            return []
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            data = payload["data"]
            list_candidates = [value for value in data if isinstance(value, list)]
            for candidate in reversed(list_candidates):
                rows = [item for item in candidate if isinstance(item, dict)]
                if rows:
                    return rows
            direct_rows = [item for item in data if isinstance(item, dict)]
            if direct_rows and any("var_id" in item or "label" in item or "value" in item or "nilai" in item for item in direct_rows):
                return direct_rows
            return []
        return self._extract_items(payload)

    def _extract_items(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            for value in payload:
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    return value
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("data", "items", "var", "variables"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            for value in payload.values():
                nested = self._extract_items(value)
                if nested:
                    return nested
        return []

    def _collect_years(self, payload: Any) -> list[str]:
        years: set[str] = set()

        def visitor(key: str, value: Any) -> None:
            if key not in {"year", "tahun", "th"} or value in (None, ""):
                return
            if isinstance(value, dict):
                label = self._item_label(value)
                if label:
                    years.add(label)
                return
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        label = self._item_label(item)
                        if label:
                            years.add(label)
                    elif isinstance(item, (str, int, float)):
                        years.add(str(item))
                return
            if isinstance(value, (str, int, float)):
                years.add(str(value))

        self._walk(payload, visitor)
        return sorted(years, reverse=True)

    def _collect_values(self, payload: Any) -> list[str]:
        datacontent_values = self._collect_datacontent_values(payload)
        if datacontent_values:
            return datacontent_values

        values: list[str] = []

        def visitor(key: str, value: Any) -> None:
            if key.lower() in {"value", "nilai"} and value not in (None, "") and isinstance(value, (str, int, float)):
                values.append(str(value))

        self._walk(payload, visitor)
        return values

    def _collect_datacontent_values(self, payload: Any) -> list[str]:
        values: list[str] = []

        def visitor(key: str, value: Any) -> None:
            if key.lower() != "datacontent" or not isinstance(value, dict):
                return
            # Sort keys numerically so rows 1..16 come out in order,
            # not in arbitrary dict insertion order.
            def _sort_key(k: str) -> tuple:
                try:
                    return (0, int(k))
                except (ValueError, TypeError):
                    return (1, str(k))

            for item_key in sorted(value.keys(), key=_sort_key):
                item = value[item_key]
                if item not in (None, "") and isinstance(item, (str, int, float)):
                    values.append(str(item))

        self._walk(payload, visitor)
        return values

    def _has_datacontent(self, payload: Any) -> bool:
        found = False

        def visitor(key: str, value: Any) -> None:
            nonlocal found
            if key.lower() == "datacontent" and isinstance(value, dict):
                found = True

        self._walk(payload, visitor)
        return found

    def _pick_latest_year(self, items: list[dict[str, Any]]) -> dict[str, Any] | None:
        year_items = [item for item in items if self._item_label(item).isdigit()]
        if year_items:
            return sorted(year_items, key=lambda item: int(self._item_label(item)), reverse=True)[0]
        return items[0] if items else None

    def _select_year_items(self, items: list[dict[str, Any]], years: list[str]) -> list[dict[str, Any]]:
        labels = {year.strip() for year in years if year.strip()}
        return [item for item in items if self._item_label(item) in labels]

    def _select_period_items(self, items: list[dict[str, Any]], periods: list[str]) -> list[dict[str, Any]]:
        if not periods:
            return []
        selected: list[dict[str, Any]] = []
        for period in periods:
            normalized_period = self._normalize_period_label(period)
            for item in items:
                if self._normalize_period_label(self._item_label(item)) == normalized_period:
                    selected.append(item)
                    break
        return selected

    def _normalize_period_label(self, text: str) -> str:
        normalized = self._normalize_text(text)
        roman_map = {" i": " 1", " ii": " 2", " iii": " 3", " iv": " 4"}
        for old, new in roman_map.items():
            normalized = normalized.replace(old, new)
        normalized = normalized.replace("triwulan", "tw")
        return normalized.strip()

    def _period_column_label(self, year: dict[str, Any], period: dict[str, Any] | None) -> str:
        year_label = self._item_label(year)
        if not period:
            return year_label
        return f"{year_label} {self._item_label(period)}"

    def _choose_row_dimension(
        self,
        vervar_items: list[dict[str, Any]],
        turvar_items: list[dict[str, Any]],
        turth_items: list[dict[str, Any]],
    ) -> tuple[str | None, list[dict[str, Any]]]:
        candidates = [
            ("turvar", turvar_items),
            ("vervar", vervar_items),
            ("turth", turth_items),
        ]
        for name, items in candidates:
            if len(items) > 1:
                return name, items
        return None, []

    def _table_row_labels(self, turvar_items: list[dict[str, Any]]) -> list[str]:
        labels = [self._item_label(item) for item in turvar_items if self._item_label(item)]
        return labels if len(labels) > 1 else []

    def _format_table_message(
        self,
        title: str,
        unit: str,
        row_labels: list[str],
        table: dict[str, list[str]],
        missing_years: list[str] | None = None,
    ) -> str:
        from app.services.wa_formatter import format_dynamic_table
        return format_dynamic_table(title, unit, row_labels, table, missing_years)

    def _format_matrix_message(
        self,
        title: str,
        table: list[list[str]],
        intro: str,
        source: str,
        note: str = "",
    ) -> str:
        from app.services.wa_formatter import format_matrix_table
        return format_matrix_table(title, table, intro, source, note)

    def _extract_table_matrix(self, payload: Any) -> list[list[str]]:
        matrix = self._find_matrix(payload)
        if matrix:
            return matrix
        html = self._find_html_table(payload)
        if html:
            parser = HtmlTableParser()
            parser.feed(html)
            if parser.tables:
                return parser.tables[0]
        rows = self._rows(payload)
        if rows:
            return self._rows_to_matrix(rows)
        return []

    def _find_matrix(self, value: Any) -> list[list[str]]:
        if isinstance(value, list):
            if value and all(isinstance(row, list) for row in value):
                matrix = [
                    [str(cell) for cell in row if cell not in (None, "")]
                    for row in value
                    if isinstance(row, list)
                ]
                if len(matrix) >= 2 and max((len(row) for row in matrix), default=0) >= 2:
                    return matrix
            for item in value:
                nested = self._find_matrix(item)
                if nested:
                    return nested
        if isinstance(value, dict):
            priority_keys = ("table", "tabel", "data", "content", "isi", "rows", "values")
            for key in priority_keys:
                if key in value:
                    nested = self._find_matrix(value[key])
                    if nested:
                        return nested
            for item in value.values():
                nested = self._find_matrix(item)
                if nested:
                    return nested
        return []

    def _find_html_table(self, value: Any) -> str:
        if isinstance(value, str) and "<table" in value.lower():
            return value
        if isinstance(value, list):
            for item in value:
                nested = self._find_html_table(item)
                if nested:
                    return nested
        if isinstance(value, dict):
            for item in value.values():
                nested = self._find_html_table(item)
                if nested:
                    return nested
        return ""

    def _rows_to_matrix(self, rows: list[dict[str, Any]]) -> list[list[str]]:
        keys = []
        for row in rows:
            for key, value in row.items():
                if key not in keys and value not in (None, "", [], {}):
                    keys.append(key)
        if not keys:
            return []
        matrix = [keys]
        for row in rows:
            matrix.append([self._normalize_cell_text(str(row.get(key, ""))) for key in keys])
        return matrix

    def _normalize_cell_text(self, text: str) -> str:
        return " ".join(text.split())

    def _pick_total_or_first(self, items: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not items:
            return None
        total_words = ("total", "jumlah", "semua", "all")
        for item in items:
            label = self._item_label(item).lower()
            if any(word in label for word in total_words):
                return item
        return items[0]

    def _pick_region_or_first(self, items: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
        if not items:
            return None
        query_lower = query.lower()
        for item in items:
            label = self._item_label(item).lower()
            if "padang pariaman" in label or label in query_lower:
                return item
        return self._pick_total_or_first(items)

    def _item_id(self, item: dict[str, Any] | None) -> str:
        if not item:
            return "0"
        for key in ("id", "val", "value", "kode", "vervar_id", "turvar_id", "turth_id", "th_id", "kode_ver_id", "item_ver_id"):
            value = item.get(key)
            if value not in (None, ""):
                return str(value)
        return self._item_label(item)

    def _item_label(self, item: dict[str, Any]) -> str:
        for key in ("label", "title", "name", "tahun", "th", "vervar", "turvar", "turth"):
            value = item.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    def _walk(self, payload: Any, visitor) -> None:
        if isinstance(payload, dict):
            for key, value in payload.items():
                visitor(str(key).lower(), value)
                self._walk(value, visitor)
        elif isinstance(payload, list):
            for item in payload:
                self._walk(item, visitor)

    def _find_first(self, payload: Any, keys: tuple[str, ...]) -> str | None:
        found: list[str] = []
        self._walk(payload, lambda key, value: found.append(str(value)) if key in keys and value else None)
        return found[0] if found else None

    def _source_url(self, domain: str, params: dict[str, str]) -> str | None:
        if not params.get("var"):
            return None
        path_params = "".join(f"/{key}/{quote(str(value))}" for key, value in self._clean_params(params).items())
        return f"{self.base_url}/list/model/data/lang/ind/domain/{quote(domain)}{path_params}/key/{{API_KEY}}"

    def _safe_url(self, url: str) -> str:
        return url.replace(self.api_key, "{API_KEY}") if self.api_key else url

    def _clean_params(self, params: dict[str, str]) -> dict[str, str]:
        return {key: value for key, value in params.items() if value not in (None, "")}

    def _cache_key(self, domain: str, model: str, params: dict[str, Any]) -> tuple[Any, ...]:
        normalized_params = tuple(sorted((key, str(value)) for key, value in params.items() if value is not None))
        return (domain, model, normalized_params)

    def _cache_get(self, key: tuple[Any, ...]) -> dict[str, Any] | None:
        if not self.cache_ttl_seconds or not self.cache_max_items:
            return None
        entry = self._cache.get(key)
        if entry:
            cached_at, payload = entry
            if time.monotonic() - cached_at <= self.cache_ttl_seconds:
                return deepcopy(payload)
            self._cache.pop(key, None)

        sqlite_payload = self._sqlite_cache_get(key)
        if sqlite_payload is not None:
            self._memory_cache_set(key, sqlite_payload)
            return deepcopy(sqlite_payload)
        return None

    def _cache_set(self, key: tuple[Any, ...], payload: dict[str, Any]) -> None:
        if not self.cache_ttl_seconds or not self.cache_max_items:
            return
        self._memory_cache_set(key, payload)
        self._sqlite_cache_set(key, payload)

    def _memory_cache_set(self, key: tuple[Any, ...], payload: dict[str, Any]) -> None:
        if len(self._cache) >= self.cache_max_items:
            oldest_key = min(self._cache, key=lambda item: self._cache[item][0])
            self._cache.pop(oldest_key, None)
        self._cache[key] = (time.monotonic(), deepcopy(payload))

    def _init_sqlite_cache(self) -> None:
        if not self.cache_db_path or not self.cache_ttl_seconds:
            return
        try:
            db_path = Path(self.cache_db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS bps_cache ("
                    "cache_key TEXT PRIMARY KEY, "
                    "cached_at REAL NOT NULL, "
                    "payload TEXT NOT NULL"
                    ")"
                )
                connection.execute("CREATE INDEX IF NOT EXISTS idx_bps_cache_cached_at ON bps_cache(cached_at)")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS bps_index ("
                    "namespace TEXT NOT NULL, "
                    "domain TEXT NOT NULL, "
                    "source_type TEXT NOT NULL, "
                    "item_key TEXT NOT NULL, "
                    "title_norm TEXT NOT NULL, "
                    "payload TEXT NOT NULL, "
                    "updated_at REAL NOT NULL, "
                    "PRIMARY KEY(namespace, domain, source_type, item_key)"
                    ")"
                )
                connection.execute("CREATE INDEX IF NOT EXISTS idx_bps_index_lookup ON bps_index(namespace, domain, source_type)")
                connection.execute("CREATE INDEX IF NOT EXISTS idx_bps_index_updated_at ON bps_index(updated_at)")
        except OSError as exc:
            logger.warning("bps.cache.sqlite_init_failed path=%s error=%s", self.cache_db_path, exc)
            self.cache_db_path = ""
        except sqlite3.Error as exc:
            logger.warning("bps.cache.sqlite_init_failed path=%s error=%s", self.cache_db_path, exc)
            self.cache_db_path = ""

    def _sqlite_cache_get(self, key: tuple[Any, ...]) -> dict[str, Any] | None:
        if not self.cache_db_path:
            return None
        storage_key = self._cache_storage_key(key)
        try:
            with sqlite3.connect(self.cache_db_path) as connection:
                row = connection.execute(
                    "SELECT cached_at, payload FROM bps_cache WHERE cache_key = ?",
                    (storage_key,),
                ).fetchone()
                if not row:
                    return None
                cached_at, payload_json = row
                if time.time() - float(cached_at) > self.cache_ttl_seconds:
                    connection.execute("DELETE FROM bps_cache WHERE cache_key = ?", (storage_key,))
                    return None
                payload = json.loads(payload_json)
                if isinstance(payload, dict):
                    logger.debug("bps.cache.sqlite_hit key=%s", storage_key)
                    return payload
        except (json.JSONDecodeError, TypeError, ValueError, sqlite3.Error) as exc:
            logger.warning("bps.cache.sqlite_get_failed error=%s", exc)
        return None

    def _sqlite_cache_set(self, key: tuple[Any, ...], payload: dict[str, Any]) -> None:
        if not self.cache_db_path:
            return
        storage_key = self._cache_storage_key(key)
        try:
            payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            with sqlite3.connect(self.cache_db_path) as connection:
                connection.execute(
                    "INSERT OR REPLACE INTO bps_cache(cache_key, cached_at, payload) VALUES (?, ?, ?)",
                    (storage_key, time.time(), payload_json),
                )
                self._sqlite_prune(connection)
        except (TypeError, ValueError, sqlite3.Error) as exc:
            logger.warning("bps.cache.sqlite_set_failed error=%s", exc)

    def _sqlite_prune(self, connection: sqlite3.Connection) -> None:
        cutoff = time.time() - self.cache_ttl_seconds
        index_cutoff = time.time() - max(self.cache_ttl_seconds * 24, self.cache_ttl_seconds)
        connection.execute("DELETE FROM bps_cache WHERE cached_at < ?", (cutoff,))
        connection.execute("DELETE FROM bps_index WHERE updated_at < ?", (index_cutoff,))
        overflow = connection.execute("SELECT COUNT(*) FROM bps_cache").fetchone()[0] - self.cache_max_items
        if overflow > 0:
            connection.execute(
                "DELETE FROM bps_cache WHERE cache_key IN ("
                "SELECT cache_key FROM bps_cache ORDER BY cached_at ASC LIMIT ?"
                ")",
                (overflow,),
            )

    def _cache_storage_key(self, key: tuple[Any, ...]) -> str:
        return json.dumps([self._cache_namespace, key], ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return (time.perf_counter() - started) * 1000
