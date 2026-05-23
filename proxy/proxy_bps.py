"""
BPS WebAPI Reverse Proxy

Jalankan di home server (IP residential Indonesia) untuk bypass
pembatasan IP cloud/VPS pada webapi.bps.go.id.

Bot di cloud server akan mengarahkan request BPS ke proxy ini
melalui Cloudflare Tunnel / Tailscale / ZeroTier.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx

app = FastAPI(title="BPS WebAPI Proxy")

BPS_BASE = "https://webapi.bps.go.id"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.api_route("/{path:path}", methods=["GET"])
async def proxy(path: str, request: Request):
    url = f"{BPS_BASE}/{path}"
    params = dict(request.query_params)
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            r = await client.get(url, params=params)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except httpx.HTTPError as e:
            return JSONResponse(content={"error": str(e)}, status_code=502)
