import hashlib
import hmac
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request

from app.config import Settings, get_settings
from app.conversation.engine import ConversationEngine
from app.conversation.session_store import SessionStore
from app.models import SessionState, UserMessage
from app.services.admin_handoff import AdminHandoffService
from app.services.ai_client import AiClient
from app.services.bps_client import BpsClient
from app.services.google_sheets_logger import GoogleSheetsLogger
from app.services.gowa_client import GowaClient


BPS_INDEX_WARMUP_QUERIES = ("penduduk", "jumlah penduduk", "ipm", "pdrb", "kemiskinan", "tpt", "tpak")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    gowa = GowaClient(settings.gowa_base_url, settings.gowa_basic_auth_user, settings.gowa_basic_auth_pass)
    app.state.sessions = SessionStore(settings.session_timeout_seconds)
    app.state.sheets = GoogleSheetsLogger(settings.google_sheets_spreadsheet_id, settings.google_service_account_json)
    app.state.recent_bot_messages = {}
    app.state.recent_inbound_messages = {}
    app.state.engine = ConversationEngine(
        AiClient(
            provider=settings.ai_provider,
            openai_api_key=settings.openai_api_key,
            openai_model=settings.openai_model,
            ollama_base_url=settings.ollama_base_url,
            ollama_model=settings.ollama_model,
        ),
        BpsClient(
            settings.bps_api_key,
            settings.bps_domain,
            cache_ttl_seconds=settings.bps_cache_ttl_seconds,
            cache_db_path=settings.bps_cache_db_path,
        ),
        AdminHandoffService(gowa, settings.admin_number_list, settings.admin_pickup_timeout_seconds),
    )
    app.state.gowa = gowa
    app.state.session_timeout_task = asyncio.create_task(_session_timeout_monitor(app, settings.session_timeout_seconds))
    app.state.bps_index_warmup_task = asyncio.create_task(_warm_bps_index(app))
    try:
        yield
    finally:
        for task in (app.state.session_timeout_task, app.state.bps_index_warmup_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Marawa BPS Padang Pariaman", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook/gowa")
async def gowa_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_signature: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    body = await request.body()
    _verify_signature(body, settings.gowa_webhook_secret, x_hub_signature_256 or x_signature)
    payload = await request.json()
    if _event_name(payload) != "message":
        return {"ok": True, "ignored": True}
    if _is_status_update(payload):
        logging.getLogger(__name__).info("webhook.gowa.ignore_status_update")
        return {"ok": True, "ignored": True, "reason": "status_update"}
    if _is_outgoing_message(payload):
        logging.getLogger(__name__).info("webhook.gowa.ignore_outgoing")
        return {"ok": True, "ignored": True, "reason": "outgoing_message"}

    message = _parse_message(payload)
    if not message.text:
        return {"ok": True, "ignored": True}
    if _is_stale_message(payload):
        logging.getLogger(__name__).info("webhook.gowa.ignore_stale_message phone=%s", message.phone if message.text else "unknown")
        return {"ok": True, "ignored": True, "reason": "stale_message"}
    if _is_bot_own_message(message.phone, settings):
        logging.getLogger(__name__).info("webhook.gowa.ignore_bot_own_number phone=%s", message.phone)
        return {"ok": True, "ignored": True, "reason": "bot_own_number"}
    if _is_duplicate_inbound(request.app.state.recent_inbound_messages, payload, message):
        logging.getLogger(__name__).info("webhook.gowa.ignore_duplicate_inbound phone=%s", message.phone)
        return {"ok": True, "ignored": True, "reason": "duplicate_inbound"}
    if _is_recent_bot_echo(request.app.state.recent_bot_messages, message.phone, message.text):
        logging.getLogger(__name__).info("webhook.gowa.ignore_recent_bot_echo phone=%s", message.phone)
        return {"ok": True, "ignored": True, "reason": "recent_bot_echo"}

    request_started = time.perf_counter()
    await _send_expired_session_timeout_notices(request.app, phone=message.phone)
    session = request.app.state.sessions.get(message.phone, message.name)
    # Refresh updated_at on message arrival to prevent background timeout monitor
    # race condition. This will be overwritten after bot sends response (the real timer start).
    request.app.state.sessions.update(session)
    if _should_send_processing_notice(session, message, request.app.state.engine.admin_handoff.admin_numbers):
        processing_message = "Sebentar, saya cari dulu datanya..."
        processing_started = time.perf_counter()
        await request.app.state.gowa.send_text(message.phone, processing_message)
        logging.getLogger(__name__).info("webhook.timing phone=%s send_ms=%.1f kind=processing", message.phone, _elapsed_ms(processing_started))
        _remember_bot_message(request.app.state.recent_bot_messages, message.phone, processing_message)
        _schedule_sheets_task(
            request.app.state.sheets.log_conversation(
                phone=message.phone,
                name=message.name,
                direction="out",
                state=session.state.value,
                intent="",
                message="",
                bot_response=processing_message,
                metadata={"processing_notice": True},
                source_url=None,
            ),
            "processing_notice",
            message.phone,
        )

    response = await request.app.state.engine.handle(session, message)

    if response.metadata.get("admin_pickup_for"):
        target_phone = response.metadata["admin_pickup_for"]
        target_session = request.app.state.sessions.get(target_phone)
        target_session.state = SessionState.TALKING_TO_ADMIN
        target_session.needs_intro = False
        request.app.state.sessions.update(target_session)
        pickup_message = request.app.state.engine.admin_pickup_user_message()
        send_started = time.perf_counter()
        await request.app.state.gowa.send_text(target_phone, pickup_message)
        logging.getLogger(__name__).info("webhook.timing phone=%s send_ms=%.1f kind=admin_pickup_user", target_phone, _elapsed_ms(send_started))
        _remember_bot_message(request.app.state.recent_bot_messages, target_phone, pickup_message)
        _schedule_sheets_task(
            request.app.state.sheets.log_conversation(
                phone=target_phone,
                name=target_session.name,
                direction="out",
                state=target_session.state.value,
                intent=response.intent.value if response.intent else "",
                message="",
                bot_response=pickup_message,
                metadata={"admin_pickup_by": message.phone},
                source_url=None,
            ),
            "admin_pickup_conversation",
            target_phone,
        )

    if response.metadata.get("admin_done_for"):
        target_phone = response.metadata["admin_done_for"]
        target_session = request.app.state.sessions.activate(target_phone)
        target_session.needs_intro = False
        user_message = request.app.state.engine.admin_finished_user_message()
        send_started = time.perf_counter()
        await request.app.state.gowa.send_text(target_phone, user_message)
        logging.getLogger(__name__).info("webhook.timing phone=%s send_ms=%.1f kind=admin_done_user", target_phone, _elapsed_ms(send_started))
        _remember_bot_message(request.app.state.recent_bot_messages, target_phone, user_message)
        _schedule_sheets_task(request.app.state.sheets.log_user(target_phone, target_session.name, target_session.total_sessions, target_session.state.value), "admin_done_user", target_phone)
        _schedule_sheets_task(
            request.app.state.sheets.log_conversation(
                phone=target_phone,
                name=target_session.name,
                direction="out",
                state=target_session.state.value,
                intent=response.intent.value if response.intent else "",
                message="",
                bot_response=user_message,
                metadata={"admin_done_by": message.phone},
                source_url=None,
            ),
            "admin_done_conversation",
            target_phone,
        )

    _schedule_sheets_task(request.app.state.sheets.log_user(message.phone, message.name, session.total_sessions, session.state.value), "log_user", message.phone)
    _schedule_sheets_task(
        request.app.state.sheets.log_conversation(
            phone=message.phone,
            name=message.name,
            direction="in",
            state=session.state.value,
            intent=response.intent.value if response.intent else "",
            message=message.text,
            bot_response=_joined_response_messages(response),
            metadata=response.metadata,
            source_url=response.source_url,
        ),
        "inbound",
        message.phone,
    )

    if response.should_send:
        response_messages = _response_messages(response)
        for index, outbound_message in enumerate(response_messages, start=1):
            send_started = time.perf_counter()
            await request.app.state.gowa.send_text(message.phone, outbound_message)
            logging.getLogger(__name__).info(
                "webhook.timing phone=%s send_ms=%.1f kind=final part=%d parts=%d",
                message.phone,
                _elapsed_ms(send_started),
                index,
                len(response_messages),
            )
            _remember_bot_message(request.app.state.recent_bot_messages, message.phone, outbound_message)
        # Refresh updated_at AFTER last message sent — timeout timer starts from here
        request.app.state.sessions.update(session)
        _schedule_sheets_task(
            request.app.state.sheets.log_conversation(
                phone=message.phone,
                name=message.name,
                direction="out",
                state=session.state.value,
                intent=response.intent.value if response.intent else "",
                message="",
                bot_response=_joined_response_messages(response),
                metadata=response.metadata,
                source_url=response.source_url,
            ),
            "outbound",
            message.phone,
        )
    logging.getLogger(__name__).info("webhook.timing phone=%s total_ms=%.1f", message.phone, _elapsed_ms(request_started))
    return {"ok": True}


def _verify_signature(body: bytes, secret: str, signature: str | None) -> None:
    if not secret:
        return
    if not signature:
        raise HTTPException(status_code=401, detail="Missing webhook signature")
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    accepted = {digest, f"sha256={digest}"}
    if signature not in accepted:
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


def _event_name(payload: dict[str, Any]) -> str:
    return str(payload.get("event") or payload.get("type") or payload.get("event_type") or "").lower()


def _is_status_update(payload: dict[str, Any]) -> bool:
    """Ignore delivery/read receipts and status broadcasts that GOWA may forward."""
    data = payload.get("payload") or payload.get("data") or payload.get("message") or payload
    # Check for status broadcast JID
    sender = str(data.get("from") or data.get("phone") or data.get("sender") or data.get("chat_jid") or "")
    if "status@broadcast" in sender:
        return True
    # Check for receipt/ack event types that may slip through as "message"
    msg_type = str(data.get("type") or data.get("message_type") or "").lower()
    if msg_type in {"receipt", "ack", "read", "delivery", "notification"}:
        return True
    return False


def _parse_message(payload: dict[str, Any]) -> UserMessage:
    data = payload.get("payload") or payload.get("data") or payload.get("message") or payload
    sender = data.get("from") or data.get("phone") or data.get("sender") or data.get("chat_jid") or ""
    phone = str(sender).split("@")[0].replace("+", "")
    text = (
        data.get("text")
        or data.get("message")
        or data.get("content")
        or data.get("body")
        or data.get("conversation")
        or ""
    )
    name = data.get("push_name") or data.get("pushName") or data.get("name") or ""
    return UserMessage(phone=phone, name=name, text=str(text))


def _is_outgoing_message(payload: dict[str, Any]) -> bool:
    data = payload.get("payload") or payload.get("data") or payload.get("message") or payload
    candidates = [
        data.get("is_from_me"),
        data.get("isFromMe"),
        data.get("from_me"),
        data.get("fromMe"),
        data.get("is_sent_by_me"),
        data.get("isSentByMe"),
        payload.get("is_from_me"),
        payload.get("isFromMe"),
        payload.get("from_me"),
        payload.get("fromMe"),
    ]
    key = data.get("key")
    if isinstance(key, dict):
        candidates.extend([key.get("fromMe"), key.get("from_me"), key.get("isFromMe"), key.get("is_from_me")])
    return any(_truthy_flag(value) for value in candidates) or _has_recursive_from_me(payload)


def _truthy_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    if isinstance(value, int):
        return value == 1
    return False


def _has_recursive_from_me(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).replace("_", "").lower()
            if normalized in {"fromme", "isfromme", "sentbyme", "issentbyme"} and _truthy_flag(item):
                return True
            if _has_recursive_from_me(item):
                return True
    if isinstance(value, list):
        return any(_has_recursive_from_me(item) for item in value)
    return False


def _remember_bot_message(cache: dict[str, list[dict[str, Any]]], phone: str, message: str) -> None:
    if phone not in cache:
        cache[phone] = []
    cache[phone].append({"message": _normalize_message(message), "ts": time.monotonic()})
    # Keep only the last 20 messages per phone to avoid unbounded growth
    if len(cache[phone]) > 20:
        cache[phone] = cache[phone][-20:]


def _should_send_processing_notice(session, message: UserMessage, admin_numbers: list[str]) -> bool:
    if message.phone in admin_numbers:
        return False
    normalized = _normalize_message(message.text)
    if not normalized or normalized in {"halo", "hai", "hi", "menu", "batal", "batalkan", "keluar"}:
        return False
    if normalized in {"data", "minta data", "cari data", "permintaan data", "1"}:
        return False
    # Navigation commands in CONFIRMING state are instant (no API call needed)
    if session.state.value == "CONFIRMING_DATA_VARIABLE":
        nav_words = {"lainnya", "lanjut", "next", "berikutnya", "hasil berikutnya", "sebelumnya", "prev", "previous", "kembali"}
        if normalized in nav_words or normalized.startswith("lainnya ") or normalized.startswith("sebelumnya "):
            return False
        if normalized.strip().isdigit():
            return False
    if session.state.value in {"ASKING_DATA_QUERY", "CONFIRMING_DATA_VARIABLE", "ASKING_DATA_YEAR"}:
        return True
    data_words = {"data", "publikasi", "simdasi", "ipm", "pdrb", "tpt", "tpak", "penduduk", "ketenagakerjaan", "kemiskinan", "inflasi"}
    return any(word in normalized.split() for word in data_words)


def _schedule_sheets_task(coro, label: str, phone: str) -> None:
    async def runner():
        started = time.perf_counter()
        try:
            await coro
        finally:
            logging.getLogger(__name__).info("webhook.timing phone=%s sheets_ms=%.1f task=%s", phone, _elapsed_ms(started), label)

    asyncio.create_task(runner())


async def _session_timeout_monitor(app: FastAPI, timeout_seconds: int) -> None:
    interval = 5
    while True:
        await asyncio.sleep(interval)
        await _send_expired_session_timeout_notices(app)


async def _warm_bps_index(app: FastAPI) -> None:
    await asyncio.sleep(5)
    for query in BPS_INDEX_WARMUP_QUERIES:
        try:
            result = await app.state.engine.bps_client.search_variable_options(query)
        except Exception:
            logging.getLogger(__name__).exception("bps.index.warmup_failed query=%s", query)
            continue
        logging.getLogger(__name__).info(
            "bps.index.warmup query=%s found=%s sources=%s",
            query,
            result.found,
            {key: len(value) for key, value in (result.metadata or {}).get("source_groups", {}).items()},
        )


async def _send_expired_session_timeout_notices(app: FastAPI, phone: str | None = None) -> None:
    for session in app.state.sessions.expired_interactive_sessions():
        if phone and session.phone != phone:
            continue
        timeout_notice = (
            "Karena tidak ada balasan selama beberapa waktu, sesi sebelumnya saya akhiri.\n\n"
            "Sesi telah berakhir."
        )
        try:
            await app.state.gowa.send_text(session.phone, timeout_notice)
        except Exception:
            logging.getLogger(__name__).exception("session_timeout.send_failed phone=%s", session.phone)
            continue
        logging.getLogger(__name__).info("session_timeout.sent phone=%s", session.phone)
        session = app.state.sessions.mark_timed_out(session)
        _remember_bot_message(app.state.recent_bot_messages, session.phone, timeout_notice)
        _schedule_sheets_task(
            app.state.sheets.log_conversation(
                phone=session.phone,
                name=session.name,
                direction="out",
                state=session.state.value,
                intent="timeout",
                message="",
                bot_response=timeout_notice,
                metadata={"session_timeout": True, "proactive": True},
                source_url=None,
            ),
            "session_timeout",
            session.phone,
        )
    # Also handle stuck WAITING_ADMIN sessions
    for session in app.state.sessions.expired_admin_handoff_sessions():
        if phone and session.phone != phone:
            continue
        admin_timeout_notice = (
            "Maaf, admin belum bisa merespons saat ini.\n\n"
            "Bot Marawa sudah aktif kembali. Silakan coba lagi nanti atau pilih layanan lain.\n\n"
            f"{app.state.engine.main_menu('Saya kembalikan ke menu utama.')}"
        )
        try:
            await app.state.gowa.send_text(session.phone, admin_timeout_notice)
        except Exception:
            logging.getLogger(__name__).exception("admin_handoff_timeout.send_failed phone=%s", session.phone)
            continue
        logging.getLogger(__name__).info("admin_handoff_timeout.sent phone=%s", session.phone)
        session.state = SessionState.MAIN_MENU
        session.handoff_started_at = None
        session.needs_intro = False
        app.state.sessions.update(session)
        _remember_bot_message(app.state.recent_bot_messages, session.phone, admin_timeout_notice)
        _schedule_sheets_task(
            app.state.sheets.log_conversation(
                phone=session.phone,
                name=session.name,
                direction="out",
                state=session.state.value,
                intent="admin_timeout",
                message="",
                bot_response=admin_timeout_notice,
                metadata={"admin_handoff_timeout": True, "proactive": True},
                source_url=None,
            ),
            "admin_handoff_timeout",
            session.phone,
        )
    # Handle TALKING_TO_ADMIN sessions where user has been idle
    for session in app.state.sessions.expired_admin_talk_sessions():
        if phone and session.phone != phone:
            continue
        admin_talk_timeout_notice = (
            "Percakapan dengan admin diakhiri karena tidak ada balasan selama beberapa waktu.\n\n"
            "Terima kasih sudah menghubungi Marawa BPS Padang Pariaman.\n\n"
            "Sampai jumpa."
        )
        try:
            await app.state.gowa.send_text(session.phone, admin_talk_timeout_notice)
        except Exception:
            logging.getLogger(__name__).exception("admin_talk_timeout.send_failed phone=%s", session.phone)
            continue
        logging.getLogger(__name__).info("admin_talk_timeout.sent phone=%s", session.phone)
        session.state = SessionState.ENDED
        session.handoff_started_at = None
        session.needs_intro = True
        app.state.sessions.update(session)
        _remember_bot_message(app.state.recent_bot_messages, session.phone, admin_talk_timeout_notice)
        _schedule_sheets_task(
            app.state.sheets.log_conversation(
                phone=session.phone,
                name=session.name,
                direction="out",
                state=session.state.value,
                intent="admin_talk_timeout",
                message="",
                bot_response=admin_talk_timeout_notice,
                metadata={"admin_talk_timeout": True, "proactive": True},
                source_url=None,
            ),
            "admin_talk_timeout",
            session.phone,
        )


def _response_messages(response) -> list[str]:
    messages = response.messages or [response.message]
    return [str(message) for message in messages if str(message).strip()]


def _joined_response_messages(response) -> str:
    return "\n\n---\n\n".join(_response_messages(response))


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _is_recent_bot_echo(cache: dict[str, list[dict[str, Any]]], phone: str, message: str, ttl_seconds: int = 120) -> bool:
    entries = cache.get(phone)
    if not entries:
        return False
    normalized = _normalize_message(message)
    now = time.monotonic()
    # Remove expired entries
    valid_entries = [entry for entry in entries if now - float(entry.get("ts", 0)) <= ttl_seconds]
    cache[phone] = valid_entries
    if not valid_entries:
        cache.pop(phone, None)
        return False
    return any(entry.get("message") == normalized for entry in valid_entries)


def _normalize_message(message: str) -> str:
    return " ".join(message.strip().split()).lower()


def _is_bot_own_message(phone: str, settings: Settings) -> bool:
    """Ignore messages coming from the bot's own WhatsApp number."""
    if not settings.bot_phone_number:
        return False
    bot_number = settings.bot_phone_number.replace("+", "").strip()
    sender_number = phone.replace("+", "").strip()
    if not bot_number or not sender_number:
        return False
    # Match exact or suffix (in case country code formatting differs)
    return sender_number == bot_number or sender_number.endswith(bot_number) or bot_number.endswith(sender_number)


def _is_stale_message(payload: dict[str, Any], max_age_seconds: int = 120) -> bool:
    """Ignore messages that are too old (e.g. replayed by GOWA after server restart)."""
    data = payload.get("payload") or payload.get("data") or payload.get("message") or payload
    # Try to extract message timestamp from various possible fields
    timestamp = None
    for key in ("timestamp", "messageTimestamp", "message_timestamp", "t", "time"):
        value = data.get(key) or payload.get(key)
        if value is not None:
            try:
                ts = int(value)
                # Timestamps in seconds (10 digits) vs milliseconds (13 digits)
                if ts > 1_000_000_000_000:
                    ts = ts // 1000
                timestamp = ts
                break
            except (ValueError, TypeError):
                continue
    if timestamp is None:
        # Also check nested key object
        key_obj = data.get("key") or data.get("message_key") or {}
        if isinstance(key_obj, dict):
            for k in ("timestamp", "messageTimestamp", "t"):
                value = key_obj.get(k)
                if value is not None:
                    try:
                        ts = int(value)
                        if ts > 1_000_000_000_000:
                            ts = ts // 1000
                        timestamp = ts
                        break
                    except (ValueError, TypeError):
                        continue
    if timestamp is None:
        return False  # Can't determine age, allow through
    now = int(time.time())
    age = now - timestamp
    return age > max_age_seconds


def _is_duplicate_inbound(cache: dict[str, float], payload: dict[str, Any], message: UserMessage, ttl_seconds: int = 60) -> bool:
    now = time.monotonic()
    expired = [key for key, ts in cache.items() if now - ts > ttl_seconds]
    for key in expired:
        cache.pop(key, None)
    normalized_text = _normalize_message(message.text)
    raw_identity = _message_identity(payload)
    identity = f"{message.phone}:{raw_identity}:{normalized_text}" if raw_identity else f"{message.phone}:{normalized_text}"
    if identity in cache:
        return True
    cache[identity] = now
    return False


def _message_identity(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized = str(key).replace("_", "").replace("-", "").lower()
            if normalized in {"id", "messageid", "messagekeyid", "stanzaid"} and isinstance(value, (str, int)):
                return str(value)
            nested = _message_identity(value)
            if nested:
                return nested
    if isinstance(payload, list):
        for item in payload:
            nested = _message_identity(item)
            if nested:
                return nested
    return None
