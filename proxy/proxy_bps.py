"""
BPS WebAPI Reverse Proxy — Playwright Edition

Pakai Playwright (Chromium headless) untuk bypass Cloudflare Managed Challenge
yang tidak bisa di-bypass dengan HTTP client biasa.

Cara kerja:
1. Request pertama: Playwright buka browser sungguhan, solve Cloudflare challenge,
   simpan cookies (termasuk cf_clearance).
2. Request berikutnya: pakai httpx dengan cookies yang sudah ada, jauh lebih cepat.
3. Kalau httpx kena challenge lagi (cookie expired), Playwright solve ulang otomatis.

Jalankan di home server (IP residential Indonesia).
"""

import asyncio
import logging
import time
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from playwright.async_api import async_playwright, Browser, BrowserContext
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("bps-proxy")

app = FastAPI(title="BPS WebAPI Proxy (Playwright)")

BPS_BASE = "https://webapi.bps.go.id"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# Browser headers lengkap yang konsisten dengan Chromium
BROWSER_HEADERS = {
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

# State global — browser instance dan cookies
_playwright = None
_browser: Optional[Browser] = None
_cf_cookies: dict = {}         # cookie name → value
_cf_cookies_ts: float = 0.0   # kapan terakhir di-refresh
CF_COOKIE_TTL = 25 * 60        # 25 menit (cf_clearance biasanya valid 30 menit)
_refresh_lock = asyncio.Lock()


@app.on_event("startup")
async def startup():
    global _playwright, _browser
    logger.info("Starting Playwright browser...")
    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ],
    )
    logger.info("Playwright browser ready")


@app.on_event("shutdown")
async def shutdown():
    global _playwright, _browser
    if _browser:
        await _browser.close()
    if _playwright:
        await _playwright.stop()
    logger.info("Playwright browser stopped")


async def _solve_cloudflare(url: str) -> dict:
    """
    Buka URL di Playwright, tunggu sampai Cloudflare challenge selesai,
    kembalikan cookies yang didapat (termasuk cf_clearance).
    """
    logger.info("Solving Cloudflare challenge via browser for: %s", url)
    context: BrowserContext = await _browser.new_context(
        user_agent=BROWSER_HEADERS["User-Agent"],
        locale="id-ID",
        timezone_id="Asia/Jakarta",
        viewport={"width": 1280, "height": 800},
        extra_http_headers={
            "Accept-Language": BROWSER_HEADERS["Accept-Language"],
        },
    )
    try:
        page = await context.new_page()
        # Buka halaman — Cloudflare akan challenge dulu
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

        # Tunggu sampai challenge selesai:
        # Cloudflare challenge page punya title "Just a moment..." atau "Attention Required"
        # Kalau udah selesai, title berubah atau elemen challenge hilang
        for _ in range(30):  # max 30 detik
            title = await page.title()
            if "Just a moment" not in title and "Attention Required" not in title:
                break
            await asyncio.sleep(1)
        else:
            logger.warning("Cloudflare challenge did not resolve in time")

        # Ambil semua cookies dari context
        cookies = await context.cookies()
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        logger.info("Got %d cookies after challenge: %s", len(cookie_dict), list(cookie_dict.keys()))
        return cookie_dict
    finally:
        await context.close()


async def _get_cf_cookies(url: str, force: bool = False) -> dict:
    """
    Kembalikan cookies yang valid. Refresh via Playwright kalau sudah expired atau force=True.
    Pakai lock supaya tidak ada dua browser yang solve challenge bersamaan.
    """
    global _cf_cookies, _cf_cookies_ts
    async with _refresh_lock:
        age = time.monotonic() - _cf_cookies_ts
        if force or not _cf_cookies or age > CF_COOKIE_TTL:
            _cf_cookies = await _solve_cloudflare(url)
            _cf_cookies_ts = time.monotonic()
    return _cf_cookies


def _is_cf_challenge(response: httpx.Response) -> bool:
    """Cek apakah response adalah Cloudflare challenge page."""
    if response.status_code != 403:
        return False
    ct = response.headers.get("content-type", "")
    if "text/html" not in ct:
        return False
    body = response.text[:512]
    return "Just a moment" in body or "Attention Required" in body or "cf_chl" in body


@app.get("/health")
async def health():
    return {"status": "ok", "browser": "playwright"}


@app.api_route("/{path:path}", methods=["GET"])
async def proxy(path: str, request: Request):
    url = f"{BPS_BASE}/{path}"
    params = dict(request.query_params)
    safe_params = {k: (v if k != "key" else "***") for k, v in params.items()}
    logger.info("proxy request: %s params=%s", url, safe_params)

    # Coba dua kali: pakai cookies cache dulu, kalau kena challenge lagi → solve ulang
    for attempt in range(2):
        force_refresh = attempt > 0
        cookies = await _get_cf_cookies(url, force=force_refresh)

        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT,
                follow_redirects=True,
                headers=BROWSER_HEADERS,
                cookies=cookies,
            ) as client:
                r = await client.get(url, params=params)
        except httpx.TimeoutException as e:
            logger.error("timeout: %s", e)
            return JSONResponse(content={"error": f"Timeout: {e}"}, status_code=504)
        except httpx.HTTPError as e:
            logger.error("http error: %s", e)
            return JSONResponse(content={"error": f"HTTP error: {e}"}, status_code=502)
        except Exception as e:
            logger.exception("unexpected error")
            return JSONResponse(content={"error": f"Unexpected error: {e}"}, status_code=502)

        logger.info(
            "bps response attempt=%d status=%d content_type=%s size=%d",
            attempt + 1, r.status_code, r.headers.get("content-type", ""), len(r.content),
        )

        # Kalau kena Cloudflare challenge lagi, solve ulang (attempt ke-2)
        if _is_cf_challenge(r):
            logger.warning("Got Cloudflare challenge on attempt %d, will retry with fresh cookies", attempt + 1)
            if attempt == 0:
                continue
            # Attempt ke-2 masih challenge → gagal
            return JSONResponse(
                content={"error": "Cloudflare challenge could not be solved"},
                status_code=503,
            )

        # Parse JSON
        try:
            data = r.json()
            return JSONResponse(content=data, status_code=r.status_code)
        except Exception:
            body_preview = r.text[:500] if r.text else "(empty)"
            logger.warning("non-json response: status=%d body=%s", r.status_code, body_preview)
            return JSONResponse(
                content={
                    "error": "BPS returned non-JSON response",
                    "status_code": r.status_code,
                    "body_preview": body_preview,
                },
                status_code=r.status_code if r.status_code >= 400 else 502,
            )

    # Seharusnya tidak sampai sini
    return JSONResponse(content={"error": "Max retries exceeded"}, status_code=503)
