"""
BPS WebAPI Reverse Proxy

Jalankan di home server (IP residential Indonesia) untuk bypass
pembatasan IP cloud/VPS pada webapi.bps.go.id.

Bot di cloud server akan mengarahkan request BPS ke proxy ini
melalui Cloudflare Tunnel / Tailscale / ZeroTier.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("bps-proxy")

app = FastAPI(title="BPS WebAPI Proxy")

BPS_BASE = "https://webapi.bps.go.id"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.api_route("/{path:path}", methods=["GET"])
async def proxy(path: str, request: Request):
    url = f"{BPS_BASE}/{path}"
    params = dict(request.query_params)
    # Jangan log API key
    safe_params = {k: (v if k != "key" else "***") for k, v in params.items()}
    logger.info("proxy request: %s params=%s", url, safe_params)

    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        try:
            r = await client.get(url, params=params)
        except httpx.TimeoutException as e:
            logger.error("timeout: %s", e)
            return JSONResponse(content={"error": f"Timeout connecting to BPS: {e}"}, status_code=504)
        except httpx.HTTPError as e:
            logger.error("http error: %s", e)
            return JSONResponse(content={"error": f"HTTP error: {e}"}, status_code=502)
        except Exception as e:
            logger.exception("unexpected error")
            return JSONResponse(content={"error": f"Unexpected error: {e}"}, status_code=502)

    logger.info("bps response: status=%d content_type=%s size=%d", r.status_code, r.headers.get("content-type", ""), len(r.content))

    # Coba parse sebagai JSON
    try:
        data = r.json()
        return JSONResponse(content=data, status_code=r.status_code)
    except Exception:
        # BPS kadang balikin HTML (misal 403 page) bukan JSON
        # Balikin raw text supaya bot bisa handle error
        body_preview = r.text[:500] if r.text else "(empty)"
        logger.warning("non-json response from BPS: status=%d body=%s", r.status_code, body_preview)
        return JSONResponse(
            content={
                "error": "BPS returned non-JSON response",
                "status_code": r.status_code,
                "body_preview": body_preview,
            },
            status_code=r.status_code if r.status_code >= 400 else 502,
        )
