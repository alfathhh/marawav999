from datetime import datetime, timedelta, timezone

import pytest

from app.conversation.engine import CONSULTATION_LINK, ConversationEngine
from app.conversation.session_store import SessionStore
from app.models import Intent, SessionState, UserMessage
from app.services.bps_client import BpsSearchResult, BpsTableResult


class FakeAi:
    def __init__(self, intent=Intent.AMBIGUOUS, query="penduduk"):
        self.intent = intent
        self.query = query

    async def classify_intent(self, text, context=None):
        if text in {"1", "data"}:
            return Intent.DATA_REQUEST
        if text == "admin":
            return Intent.ADMIN
        if text == "keluar":
            return Intent.EXIT
        return self.intent

    async def extract_data_query(self, text):
        return self.query


class GenericDataAi(FakeAi):
    async def parse_service_request(self, text, context=None):
        if text == "minta data":
            return {"intent": "data_request", "query": "", "keywords": [], "years": [], "periods": [], "safe": True}
        return {"intent": "data_request", "query": text, "keywords": [], "years": [], "periods": [], "safe": True}


class FakeBps:
    def __init__(self, result, periods=None, matches=None, table_results=None):
        self.result = result
        self.periods = periods or []
        self.last_periods = []
        self.last_years = []
        self.search_calls = []
        self.table_results = list(table_results or [])
        matches = [{"var_id": 1, "title": "Jumlah Penduduk", "unit": "jiwa"}] if matches is None else matches
        self.options = BpsSearchResult(
            True,
            "Berikut beberapa data yang mirip.\n\n1. Jumlah Penduduk\n\nTolong ketik nomor pilihannya.",
            metadata={"matches": matches},
            too_many=True,
        )
        if matches == []:
            self.options = BpsSearchResult(
                True,
                "Data tabel dinamis belum ditemukan, tetapi ada tabel SIMDASI terkait: Statistik Ketenagakerjaan.",
                metadata={"simdasi": {"id": 1, "title": "Statistik Ketenagakerjaan"}},
            )

    async def search_variable_options(self, query, **kwargs):
        self.search_calls.append(query)
        return self.options if self.result.found else self.result

    def pick_candidate(self, text, candidates):
        if text.strip().isdigit() and candidates:
            index = int(text.strip()) - 1
            return candidates[index] if 0 <= index < len(candidates) else None
        return None

    async def get_period_options(self, variable):
        return self.periods

    def has_quarterly_periods(self, periods):
        return any("Triwulan" in item.get("turth", "") for item in periods)

    async def fetch_table_by_variable(self, query, variable, years, periods=None):
        self.last_years = years
        self.last_periods = periods or []
        if self.table_results:
            return self.table_results.pop(0)
        return BpsTableResult(True, "Jumlah Penduduk\nSatuan: jiwa\n\n```text\nRincian | 2023\nTotal   | 1000\n```")


class FakeHandoff:
    def __init__(self):
        self.started = False
        self.expired = False
        self.admin_numbers = ["628admin"]

    async def start(self, session):
        self.started = True
        session.state = SessionState.WAITING_ADMIN
        session.handoff_started_at = datetime.now(timezone.utc)

    def is_pickup_expired(self, session):
        return self.expired

    def is_admin_command(self, text, sender):
        return sender in {"628admin"} and text.startswith("selesai")

    def parse_finished_user(self, text):
        parts = text.split(maxsplit=1)
        return parts[1] if len(parts) == 2 else ""


def message(text="halo"):
    return UserMessage(phone="6281", name="Tester", text=text)


def active_session():
    session = SessionStore().get("6281", "Tester")
    session.needs_intro = False
    return session


@pytest.mark.asyncio
async def test_new_session_always_returns_greeting_before_processing_message():
    engine = ConversationEngine(
        FakeAi(Intent.DATA_REQUEST, "penduduk"),
        FakeBps(BpsSearchResult(True, "unused")),
        FakeHandoff(),
    )
    session = SessionStore().get("6281", "Tester")

    response = await engine.handle(session, message("jumlah penduduk"))

    assert "Halo, saya Marawa BPS Padang Pariaman." in response.message
    assert "Saya siap membantu layanan berikut" in response.message
    assert "Silakan ketik nomor pilihannya" not in response.message
    assert "Saya belum yakin maksudnya" not in response.message
    assert not session.needs_intro
    assert session.state == SessionState.MAIN_MENU


@pytest.mark.asyncio
async def test_main_menu_to_data_request_options_after_session_opened():
    engine = ConversationEngine(
        FakeAi(Intent.DATA_REQUEST, "penduduk"),
        FakeBps(BpsSearchResult(True, "Data penduduk\nURL sumber: https://example.test", "https://example.test")),
        FakeHandoff(),
    )
    session = active_session()

    response = await engine.handle(session, message("1"))

    assert "Silakan ketik nomor pilihannya" in response.message
    assert not session.needs_intro
    assert session.state == SessionState.CONFIRMING_DATA_VARIABLE


@pytest.mark.asyncio
async def test_generic_data_request_asks_query_without_searching():
    fake_bps = FakeBps(BpsSearchResult(True, "unused"))
    engine = ConversationEngine(GenericDataAi(Intent.DATA_REQUEST, ""), fake_bps, FakeHandoff())
    session = active_session()

    response = await engine.handle(session, message("minta data"))

    assert "Boleh. Data apa yang ingin dicari?" in response.message
    assert "Ketik batal untuk kembali." in response.message
    assert "Ketik menu untuk ke menu utama." in response.message
    assert session.state == SessionState.ASKING_DATA_QUERY
    assert fake_bps.search_calls == []


@pytest.mark.asyncio
async def test_session_greeting_only_sent_once_per_session():
    engine = ConversationEngine(
        FakeAi(Intent.DATA_REQUEST, "penduduk"),
        FakeBps(BpsSearchResult(True, "unused")),
        FakeHandoff(),
    )
    session = SessionStore().get("6281", "Tester")

    first = await engine.handle(session, message("data penduduk"))
    second = await engine.handle(session, message("data penduduk"))

    assert "Halo, saya Marawa BPS Padang Pariaman." in first.message
    assert "Silakan ketik nomor pilihannya" not in first.message
    assert "Halo, saya Marawa BPS Padang Pariaman." not in second.message
    assert "Silakan ketik nomor pilihannya" in second.message


@pytest.mark.asyncio
async def test_greeting_returns_intro_without_ambiguous_suffix():
    engine = ConversationEngine(
        FakeAi(Intent.AMBIGUOUS),
        FakeBps(BpsSearchResult(False, "")),
        FakeHandoff(),
    )
    session = SessionStore().get("6281", "Tester")

    response = await engine.handle(session, message("halo"))

    assert "Halo, saya Marawa BPS Padang Pariaman." in response.message
    assert "Saya siap membantu layanan berikut" in response.message
    assert "Saya belum yakin maksudnya" not in response.message
    assert not session.needs_intro


@pytest.mark.asyncio
async def test_repeated_greeting_returns_menu_without_ambiguous_suffix():
    engine = ConversationEngine(
        FakeAi(Intent.AMBIGUOUS),
        FakeBps(BpsSearchResult(False, "")),
        FakeHandoff(),
    )
    session = active_session()

    await engine.handle(session, message("halo"))
    response = await engine.handle(session, message("halo"))

    assert "1. Mencari data statistik BPS Kabupaten Padang Pariaman" in response.message
    assert "Saya belum yakin maksudnya" not in response.message


@pytest.mark.asyncio
async def test_data_not_found_points_to_consultation_link():
    engine = ConversationEngine(FakeAi(Intent.DATA_REQUEST), FakeBps(BpsSearchResult(False, "not found")), FakeHandoff())
    session = active_session()

    response = await engine.handle(session, message("data"))

    assert "kata kunci yang lebih spesifik" in response.message
    assert response.source_url == CONSULTATION_LINK
    assert session.state == SessionState.ASKING_DATA_QUERY


@pytest.mark.asyncio
async def test_data_source_error_is_shown_to_user():
    engine = ConversationEngine(
        FakeAi(Intent.DATA_REQUEST),
        FakeBps(BpsSearchResult(False, "BPS WebAPI sedang bermasalah saat mencari publikasi.", metadata={"publication_error": "500"})),
        FakeHandoff(),
    )
    session = active_session()

    response = await engine.handle(session, message("data publikasi"))

    assert "BPS WebAPI sedang bermasalah" in response.message
    assert "data tersebut belum ditemukan" not in response.message
    assert response.source_url == CONSULTATION_LINK
    assert session.state == SessionState.ASKING_DATA_QUERY


@pytest.mark.asyncio
async def test_data_query_can_return_simdasi_summary_without_variable_selection():
    engine = ConversationEngine(
        FakeAi(Intent.DATA_REQUEST, "ketenagakerjaan"),
        FakeBps(BpsSearchResult(True, "unused"), matches=[]),
        FakeHandoff(),
    )
    session = active_session()

    response = await engine.handle(session, message("data ketenagakerjaan"))

    assert "SIMDASI" in response.message
    assert "Saya kembalikan ke menu utama" in response.message
    assert session.state == SessionState.MAIN_MENU
    assert session.pending_bps_matches == []


@pytest.mark.asyncio
async def test_guided_data_flow_selects_option_then_year_table():
    engine = ConversationEngine(
        FakeAi(Intent.DATA_REQUEST, "penduduk"),
        FakeBps(BpsSearchResult(True, "unused")),
        FakeHandoff(),
    )
    session = active_session()

    options = await engine.handle(session, message("data penduduk"))
    assert session.state == SessionState.CONFIRMING_DATA_VARIABLE
    assert "Silakan ketik nomor pilihannya" in options.message

    confirm = await engine.handle(session, message("1"))
    assert session.state == SessionState.ASKING_DATA_YEAR
    assert "Tahun berapa" in confirm.message

    table = await engine.handle(session, message("2023"))
    assert session.state == SessionState.MAIN_MENU
    assert "```text" in table.message
    assert "Saya kembalikan ke menu utama" in table.message
    assert len(table.messages) == 3
    assert "```text" in table.messages[0]
    assert "1. Mencari data statistik BPS Kabupaten Padang Pariaman" in table.messages[1]
    assert CONSULTATION_LINK in table.messages[2]


@pytest.mark.asyncio
async def test_publication_search_opens_selected_publication_without_asking_year():
    matches = [
        {"source_type": "publication", "source_label": "Publikasi", "title": "Kecamatan 2x11 Kayu Tanam Dalam Angka 2026"},
        {"source_type": "publication", "source_label": "Publikasi", "title": "Kabupaten Padang Pariaman Dalam Angka 2026"},
    ]
    fake_bps = FakeBps(
        BpsSearchResult(True, "unused"),
        matches=matches,
        table_results=[
            BpsTableResult(
                True,
                "[Publikasi] Kecamatan 2x11 Kayu Tanam Dalam Angka 2026\n\n"
                "Tanggal rilis: 2026-09-26\n\n"
                "Link publikasi:\nhttps://padangpariamankab.bps.go.id/id/publication/2026/09/26/abc/kecamatan-2x11-kayu-tanam-dalam-angka-2026.html\n\n"
                "Sumber: BPS Kabupaten Padang Pariaman via WebAPI.",
            )
        ],
    )
    engine = ConversationEngine(
        FakeAi(Intent.DATA_REQUEST, "kecamatan dalam angka"),
        fake_bps,
        FakeHandoff(),
    )
    session = active_session()

    options = await engine.handle(session, message("publikasi kecamatan dalam angka"))
    assert session.state == SessionState.CONFIRMING_DATA_VARIABLE
    assert "1. [Publikasi] Kecamatan 2x11 Kayu Tanam Dalam Angka 2026" in options.message

    confirm = await engine.handle(session, message("1"))
    assert session.state == SessionState.MAIN_MENU
    assert fake_bps.last_years == []
    assert "[Publikasi] Kecamatan 2x11 Kayu Tanam Dalam Angka 2026" in confirm.message
    assert "Tahun berapa" not in confirm.message
    assert "Saya kembalikan ke menu utama" in confirm.message


@pytest.mark.asyncio
async def test_guided_data_flow_uses_years_from_initial_message_after_selection():
    fake_bps = FakeBps(BpsSearchResult(True, "unused"))
    engine = ConversationEngine(FakeAi(Intent.DATA_REQUEST, "tpt"), fake_bps, FakeHandoff())
    session = active_session()

    options = await engine.handle(session, message("butuh data tpt 2020-2021"))
    assert "Silakan ketik nomor pilihannya" in options.message
    assert session.pending_data_years == ["2020", "2021"]

    table = await engine.handle(session, message("1"))

    assert fake_bps.last_years == ["2020", "2021"]
    assert session.state == SessionState.MAIN_MENU
    assert "```text" in table.message


@pytest.mark.asyncio
async def test_guided_data_flow_keeps_context_after_unavailable_year():
    fake_bps = FakeBps(
        BpsSearchResult(True, "unused"),
        table_results=[
            BpsTableResult(
                False,
                "Tahun yang diminta belum tersedia. Tahun tersedia: 2021, 2020, 2019, 2018",
                metadata={"reason": "year_unavailable"},
            ),
            BpsTableResult(True, "TPAK\nSatuan: persen\n\n```text\nRincian | 2018 | 2019\nTotal   | 50   | 51\n```"),
        ],
    )
    engine = ConversationEngine(FakeAi(Intent.DATA_REQUEST, "tpak"), fake_bps, FakeHandoff())
    session = active_session()

    await engine.handle(session, message("data tpak"))
    await engine.handle(session, message("1"))
    unavailable = await engine.handle(session, message("2023-2025"))

    assert session.state == SessionState.ASKING_DATA_YEAR
    assert session.selected_bps_variable["title"] == "Jumlah Penduduk"
    assert session.pending_data_years == ["2023", "2024", "2025"]
    assert "ketik tahun atau rentang tahun lain" in unavailable.message

    table = await engine.handle(session, message("2018-2019"))

    assert fake_bps.last_years == ["2018", "2019"]
    assert session.state == SessionState.MAIN_MENU
    assert "```text" in table.message


@pytest.mark.asyncio
async def test_guided_data_flow_answers_followup_about_missing_years():
    fake_bps = FakeBps(
        BpsSearchResult(True, "unused"),
        table_results=[
            BpsTableResult(
                True,
                "TPT\nSatuan: persen\n\n```text\nRincian | 2021 | 2020\nTotal   | 6.05 | 7.69\n```\n\n"
                "Sumber: BPS Kabupaten Padang Pariaman via WebAPI.\n\n"
                "Catatan: data tahun 2022, 2023, 2024, 2025 belum tersedia di WebAPI BPS untuk tabel ini.",
                metadata={
                    "variable": {"var_id": 1, "title": "TPT Laki-Laki"},
                    "requested_years": ["2020", "2021", "2022", "2023", "2024", "2025"],
                    "displayed_years": ["2021", "2020"],
                    "missing_years": ["2022", "2023", "2024", "2025"],
                },
            ),
        ],
    )
    engine = ConversationEngine(FakeAi(Intent.DATA_REQUEST, "tpt"), fake_bps, FakeHandoff())
    session = active_session()

    await engine.handle(session, message("data"))
    await engine.handle(session, message("data tpt"))
    await engine.handle(session, message("1"))
    table = await engine.handle(session, message("2020-2025"))

    assert session.state == SessionState.MAIN_MENU
    assert "Catatan: data tahun 2022" in table.message

    followup = await engine.handle(session, message("loh 2022-2025 mana"))

    assert "belum tersedia" in followup.message
    assert "2022, 2023, 2024, 2025" in followup.message
    assert "2021, 2020" in followup.message


@pytest.mark.asyncio
async def test_guided_data_flow_can_page_search_options():
    matches = [{"var_id": index, "title": f"Pilihan {index}", "unit": "jiwa"} for index in range(1, 8)]
    engine = ConversationEngine(
        FakeAi(Intent.DATA_REQUEST, "penduduk"),
        FakeBps(BpsSearchResult(True, "unused"), matches=matches),
        FakeHandoff(),
    )
    session = active_session()

    first_page = await engine.handle(session, message("data penduduk"))
    assert "1. [Tabel Dinamis] Pilihan 1" in first_page.message
    assert "5. [Tabel Dinamis] Pilihan 5" in first_page.message
    assert "6. [Tabel Dinamis] Pilihan 6" not in first_page.message
    assert "Ketik lainnya" in first_page.message

    second_page = await engine.handle(session, message("lainnya"))
    assert "1. [Tabel Dinamis] Pilihan 6" in second_page.message
    assert "2. [Tabel Dinamis] Pilihan 7" in second_page.message
    assert "Ketik sebelumnya" in second_page.message

    confirm = await engine.handle(session, message("1"))
    assert session.selected_bps_variable["var_id"] == 6
    assert "Pilihan 6" in confirm.message


@pytest.mark.asyncio
async def test_guided_data_flow_for_quarterly_variable_asks_quarter():
    fake_bps = FakeBps(
        BpsSearchResult(True, "unused"),
        periods=[
            {"turth_id": 31, "turth": "Triwulan I"},
            {"turth_id": 32, "turth": "Triwulan II"},
            {"turth_id": 35, "turth": "Tahunan"},
        ],
    )
    engine = ConversationEngine(FakeAi(Intent.DATA_REQUEST, "pdrb"), fake_bps, FakeHandoff())
    session = active_session()

    await engine.handle(session, message("data pdrb"))
    confirm = await engine.handle(session, message("1"))
    assert "Tahun dan triwulan" in confirm.message

    ask_period = await engine.handle(session, message("2024"))
    assert session.state == SessionState.ASKING_DATA_YEAR
    assert "tersedia per triwulan" in ask_period.message

    table = await engine.handle(session, message("TW 1-2"))
    assert session.state == SessionState.MAIN_MENU
    assert fake_bps.last_periods == ["Triwulan I", "Triwulan II"]
    assert "```text" in table.message


@pytest.mark.asyncio
async def test_admin_handoff_cancel_done_and_timeout():
    handoff = FakeHandoff()
    engine = ConversationEngine(FakeAi(Intent.ADMIN), FakeBps(BpsSearchResult(False, "")), handoff)
    session = active_session()

    response = await engine.handle(session, message("admin"))
    assert handoff.started
    assert session.state == SessionState.WAITING_ADMIN
    assert "admin" in response.message.lower()

    response = await engine.handle(session, message("batal"))
    assert session.state == SessionState.MAIN_MENU
    assert "menu utama" in response.message

    session.state = SessionState.WAITING_ADMIN
    session.handoff_started_at = datetime.now(timezone.utc) - timedelta(minutes=6)
    handoff.expired = True
    response = await engine.handle(session, message("halo admin?"))
    assert "admin sedang sibuk" in response.message.lower()
    assert session.state == SessionState.MAIN_MENU


@pytest.mark.asyncio
async def test_admin_command_without_target_returns_instruction_without_intro():
    engine = ConversationEngine(FakeAi(Intent.EXIT), FakeBps(BpsSearchResult(False, "")), FakeHandoff())
    session = SessionStore().get("628admin", "Admin")

    response = await engine.handle(session, UserMessage(phone="628admin", name="Admin", text="selesai"))

    assert "Format command admin" in response.message
    assert "Halo, saya Marawa" not in response.message
    assert response.metadata["admin_command_error"] == "missing_target"


@pytest.mark.asyncio
async def test_admin_sender_never_receives_session_intro_for_regular_reply():
    engine = ConversationEngine(FakeAi(Intent.AMBIGUOUS), FakeBps(BpsSearchResult(False, "")), FakeHandoff())
    session = SessionStore().get("628admin", "Admin")

    response = await engine.handle(session, UserMessage(phone="628admin", name="Admin", text="halo"))

    assert "Halo, saya Marawa" not in response.message
    assert "saya belum yakin" in response.message.lower()
    assert not session.needs_intro


def test_admin_finished_user_message_returns_intro_and_menu():
    message_text = ConversationEngine.admin_finished_user_message()

    assert "Admin telah menyelesaikan sesi bantuan" in message_text
    assert "Halo, saya Marawa BPS Padang Pariaman." in message_text
    assert "Silakan pilih layanan yang dibutuhkan." in message_text
    assert "1. Mencari data statistik BPS Kabupaten Padang Pariaman" in message_text


def test_general_timeout_starts_new_session():
    store = SessionStore(timeout_seconds=600)
    session = store.get("6281")
    session.updated_at = datetime.now(timezone.utc) - timedelta(seconds=601)

    fresh = store.get("6281")

    assert fresh.total_sessions == 2
    assert fresh.needs_intro
    assert not fresh.timeout_notice_pending


def test_store_finds_and_marks_idle_sessions_for_proactive_timeout():
    store = SessionStore(timeout_seconds=600)
    idle = store.get("6281", "Tester")
    idle.needs_intro = False
    idle.updated_at = datetime.now(timezone.utc) - timedelta(seconds=601)
    waiting_admin = store.get("6282", "Admin waiting")
    waiting_admin.state = SessionState.WAITING_ADMIN
    waiting_admin.updated_at = datetime.now(timezone.utc) - timedelta(seconds=601)

    expired = store.expired_interactive_sessions()

    assert expired == [idle]
    timed_out = store.mark_timed_out(idle)
    assert timed_out.state == SessionState.ENDED
    assert timed_out.needs_intro
    assert not timed_out.timeout_notice_pending
    assert store.expired_interactive_sessions() == []


@pytest.mark.asyncio
async def test_expired_session_does_not_wait_for_next_message_to_show_timeout_notice():
    store = SessionStore(timeout_seconds=600)
    session = store.get("6281", "Tester")
    session.needs_intro = False
    session.updated_at = datetime.now(timezone.utc) - timedelta(seconds=601)
    fresh = store.get("6281", "Tester")
    engine = ConversationEngine(FakeAi(Intent.AMBIGUOUS), FakeBps(BpsSearchResult(False, "")), FakeHandoff())

    first = await engine.handle(fresh, message("halo"))

    assert "sesi sebelumnya saya akhiri" not in first.message
    assert "Halo, saya Marawa" in first.message
    assert not fresh.timeout_notice_pending
