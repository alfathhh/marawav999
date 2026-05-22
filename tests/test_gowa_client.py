import pytest
import respx
from httpx import Response

from app.services.gowa_client import GowaClient, MAX_WHATSAPP_TEXT_CHARS


def test_gowa_client_chunks_long_messages_without_losing_content():
    client = GowaClient("http://gowa:3000")
    message = ("A" * 1200 + "\n\n") * 8

    chunks = client._message_chunks(message)

    assert len(chunks) > 1
    assert all(len(chunk) <= MAX_WHATSAPP_TEXT_CHARS for chunk in chunks)
    assert "".join(chunks) == message


@pytest.mark.asyncio
@respx.mock
async def test_gowa_client_sends_all_chunks():
    client = GowaClient("http://gowa:3000")
    route = respx.post("http://gowa:3000/send/message").mock(return_value=Response(200, json={"ok": True}))

    await client.send_text("6281", "B" * (MAX_WHATSAPP_TEXT_CHARS + 10))

    assert route.call_count == 2
