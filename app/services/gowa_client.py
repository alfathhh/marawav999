import httpx


MAX_WHATSAPP_TEXT_CHARS = 3500


class GowaClient:
    def __init__(self, base_url: str, username: str = "", password: str = ""):
        self.base_url = base_url.rstrip("/")
        self.auth = (username, password) if username and password else None

    async def send_text(self, to: str, message: str) -> None:
        async with httpx.AsyncClient(timeout=20, auth=self.auth) as client:
            for chunk in self._message_chunks(message):
                payload = {"phone": to, "message": chunk}
                response = await client.post(f"{self.base_url}/send/message", json=payload)
                response.raise_for_status()

    def _message_chunks(self, message: str) -> list[str]:
        text = str(message or "")
        if len(text) <= MAX_WHATSAPP_TEXT_CHARS:
            return [text]
        chunks: list[str] = []
        remaining = text
        while len(remaining) > MAX_WHATSAPP_TEXT_CHARS:
            split_at = remaining.rfind("\n\n", 0, MAX_WHATSAPP_TEXT_CHARS + 1)
            if split_at < MAX_WHATSAPP_TEXT_CHARS // 2:
                split_at = remaining.rfind("\n", 0, MAX_WHATSAPP_TEXT_CHARS + 1)
            if split_at < MAX_WHATSAPP_TEXT_CHARS // 2:
                split_at = remaining.rfind(" ", 0, MAX_WHATSAPP_TEXT_CHARS + 1)
            if split_at < MAX_WHATSAPP_TEXT_CHARS // 2:
                split_at = MAX_WHATSAPP_TEXT_CHARS
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:]
        if remaining:
            chunks.append(remaining)
        return chunks
