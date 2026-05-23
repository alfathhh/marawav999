from app.conversation.parsing import parse_quarter_periods, parse_years
from app.models import BotResponse, Intent, Session, SessionState, UserMessage
from app.services.admin_handoff import AdminHandoffService
from app.services.ai_client import AiClient
from app.services.bps_client import BpsClient
from app.services.guarded_agent import AgentDecision, GuardedDataAgent


CONSULTATION_LINK = "https://s.bps.go.id/tamu1306"
DATA_OPTION_PAGE_SIZE = 5
SOURCE_ORDER = ("dynamic_table", "simdasi", "publication")
SOURCE_LABELS = {
    "dynamic_table": "Tabel Dinamis",
    "simdasi": "SIMDASI",
    "publication": "Publikasi",
}


class ConversationEngine:
    def __init__(self, ai_client: AiClient, bps_client: BpsClient, admin_handoff: AdminHandoffService):
        self.ai_client = ai_client
        self.agent = GuardedDataAgent(ai_client)
        self.bps_client = bps_client
        self.admin_handoff = admin_handoff

    async def handle(self, session: Session, user_message: UserMessage) -> BotResponse:
        text = user_message.text.strip()
        lowered = text.lower()
        session.history.append({"user": text})
        if self._is_greeting(lowered) and user_message.phone not in self.admin_handoff.admin_numbers:
            session.state = SessionState.MAIN_MENU
            session.handoff_started_at = None
            self._clear_data_context(session)
            if session.needs_intro:
                session.needs_intro = False
                return self._with_timeout_notice(
                    session,
                    BotResponse(self.intro_message(), Intent.MENU, metadata={"session_intro": True, "new_session_greeting": True}),
                    user_message.phone,
                )
            return self._with_timeout_notice(session, BotResponse(self.main_menu(), Intent.MENU, metadata={"greeting": True}), user_message.phone)
        response = await self._handle_message(session, user_message, text, lowered)
        response = self._with_session_intro(session, response, user_message.phone)
        return self._with_timeout_notice(session, response, user_message.phone)

    async def _handle_message(self, session: Session, user_message: UserMessage, text: str, lowered: str) -> BotResponse:
        sender_is_admin = user_message.phone in self.admin_handoff.admin_numbers
        if self.admin_handoff.is_admin_command(text, user_message.phone):
            target = self.admin_handoff.parse_finished_user(text)
            session.needs_intro = False
            if not target:
                return BotResponse(
                    "Format command admin belum lengkap.\n\n"
                    "Gunakan format:\n"
                    "selesai <nomor_user>\n\n"
                    "Contoh:\n"
                    "selesai 628112144442",
                    Intent.ADMIN,
                    metadata={"admin_command_error": "missing_target"},
                )
            return BotResponse(f"Baik, bot untuk {target} sudah saya aktifkan kembali.", Intent.ADMIN, metadata={"admin_done_for": target})

        if session.needs_intro and not sender_is_admin:
            session.state = SessionState.MAIN_MENU
            session.handoff_started_at = None
            self._clear_data_context(session)
            session.needs_intro = False
            return BotResponse(self.intro_message(), Intent.MENU, metadata={"session_intro": True, "new_session_greeting": True})

        if lowered in {"menu", "batal", "batalkan"}:
            session.state = SessionState.MAIN_MENU
            session.handoff_started_at = None
            self._clear_data_context(session)
            return BotResponse(self.main_menu("Baik, saya kembalikan ke menu utama."), Intent.MENU)

        if lowered == "keluar":
            session.state = SessionState.ENDED
            return BotResponse("Terima kasih sudah menghubungi Marawa BPS Padang Pariaman.\n\nSampai jumpa.", Intent.EXIT)

        if session.state == SessionState.WAITING_ADMIN:
            if self.admin_handoff.is_pickup_expired(session):
                session.state = SessionState.MAIN_MENU
                session.handoff_started_at = None
                return BotResponse(self.main_menu("Maaf, admin sedang sibuk saat ini. Silakan coba lagi nanti."), Intent.ADMIN)
            return BotResponse("", should_send=False)

        if session.state == SessionState.ASKING_DATA_QUERY:
            if self._is_generic_data_request(text):
                return BotResponse(self._with_submenu_navigation("Boleh. Data apa yang ingin dicari?"), Intent.DATA_REQUEST)
            decision = await self.agent.plan(session, text)
            guarded_response = self._guarded_agent_response(decision)
            if guarded_response:
                return guarded_response
            return await self._show_data_options(
                session,
                decision.query or text,
                keywords=decision.keywords,
                years=decision.years,
                periods=decision.periods,
            )

        if session.state == SessionState.CONFIRMING_DATA_VARIABLE:
            return await self._confirm_data_variable(session, text)

        if session.state == SessionState.ASKING_DATA_YEAR:
            return await self._answer_data_table(session, text)

        if session.state == SessionState.MAIN_MENU and self._looks_like_data_followup(text, session):
            return self._data_response(self._answer_last_data_followup(text, session), metadata=session.last_data_context)

        decision = await self.agent.plan(session, text)
        guarded_response = self._guarded_agent_response(decision)
        if guarded_response:
            return guarded_response

        intent = self._intent_from_parsed({"intent": decision.intent})
        if intent == Intent.DATA_REQUEST:
            extracted = decision.query
            if extracted and not self._is_generic_data_request(extracted) and len(extracted) >= 3:
                return await self._show_data_options(
                    session,
                    extracted,
                    keywords=decision.keywords,
                    years=decision.years,
                    periods=decision.periods,
                )
            session.state = SessionState.ASKING_DATA_QUERY
            return BotResponse(self._with_submenu_navigation("Boleh. Data apa yang ingin dicari?"), intent)
        if intent == Intent.CONSULTATION:
            return BotResponse(
                "Untuk rekomendasi dan konsultasi statistik, silakan isi buku tamu PST melalui tautan berikut:\n"
                f"{CONSULTATION_LINK}\n\n"
                "Kalau ingin dibantu petugas, ketik admin.\n\n"
                f"{self._submenu_navigation_text()}",
                intent,
                source_url=CONSULTATION_LINK,
            )
        if intent == Intent.ADMIN:
            await self.admin_handoff.start(session)
            return BotResponse(
                "Baik, saya hubungkan ke admin.\n\n"
                "Untuk sementara, bot tidak akan membalas percakapan ini sampai admin selesai membantu.\n\n"
                "Kalau ingin membatalkan, ketik batal atau menu.",
                intent,
            )
        if intent == Intent.EXIT:
            session.state = SessionState.ENDED
            return BotResponse("Terima kasih sudah menghubungi Marawa BPS Padang Pariaman.\n\nSampai jumpa.", intent)
        if intent == Intent.MENU:
            session.state = SessionState.MAIN_MENU
            return BotResponse(self.main_menu(), intent)
        return BotResponse(
            "Maaf, saya belum yakin maksudnya.\n\n"
            "Silakan pilih salah satu layanan: data, konsultasi statistik, admin, atau keluar.",
            Intent.AMBIGUOUS,
        )

    def _guarded_agent_response(self, decision: AgentDecision) -> BotResponse | None:
        if decision.action == "reject_unsafe":
            return BotResponse(
                "Maaf, saya tidak bisa mengikuti instruksi yang mencoba mengubah aturan sistem, membuka rahasia, atau menjalankan alat internal. "
                "Saya tetap bisa membantu mencari data statistik BPS Padang Pariaman.",
                Intent.AMBIGUOUS,
                metadata=decision.metadata,
            )
        if decision.action == "reject_out_of_scope":
            return BotResponse(
                "Maaf, layanan ini khusus untuk data statistik BPS Padang Pariaman.\n\n"
                "Saya bisa membantu mencari data, memberi arahan konsultasi statistik, atau menghubungkan Anda dengan admin.",
                Intent.AMBIGUOUS,
                metadata=decision.metadata,
            )
        return None

    def _data_response(
        self,
        message: str,
        source_url: str | None = None,
        metadata: dict | None = None,
        include_menu: bool = False,
    ) -> BotResponse:
        metadata = metadata or {}
        if include_menu:
            parts = [
                message,
                self.main_menu("Saya kembalikan ke menu utama, ya."),
                self._data_help_footer(),
            ]
            return BotResponse("\n\n".join(parts), Intent.DATA_REQUEST, source_url, metadata, messages=parts)
        return BotResponse(self._with_data_help_footer(message), Intent.DATA_REQUEST, source_url, metadata)

    def _with_data_help_footer(self, message: str) -> str:
        footer = self._data_help_footer()
        if footer in message:
            return message
        return f"{message}\n\n{footer}"

    def _with_submenu_navigation(self, message: str) -> str:
        navigation = self._submenu_navigation_text()
        if navigation in message:
            return message
        return f"{message}\n\n{navigation}"

    def _data_help_footer(self) -> str:
        return f"Jika data yang dibutuhkan belum ditemukan, Anda juga bisa mengajukan permintaan data melalui {CONSULTATION_LINK}"

    def _submenu_navigation_text(self) -> str:
        return "Ketik batal untuk kembali.\nKetik menu untuk ke menu utama."

    def _with_return_to_menu(self, message: str) -> str:
        menu = self.main_menu("Saya kembalikan ke menu utama, ya.")
        if "Saya kembalikan ke menu utama" in message:
            return message
        return f"{message}\n\n{menu}"

    def _with_session_intro(self, session: Session, response: BotResponse, sender: str) -> BotResponse:
        if sender in self.admin_handoff.admin_numbers:
            session.needs_intro = False
            return response
        if not session.needs_intro or not response.should_send:
            return response
        session.needs_intro = False
        response.message = f"{self.intro_message()}\n\n{response.message}"
        response.metadata = {**response.metadata, "session_intro": True}
        return response

    def _with_timeout_notice(self, session: Session, response: BotResponse, sender: str) -> BotResponse:
        if sender in self.admin_handoff.admin_numbers:
            session.timeout_notice_pending = False
            return response
        if not session.timeout_notice_pending or not response.should_send:
            return response
        session.timeout_notice_pending = False
        notice = (
            "Karena tidak ada balasan selama beberapa waktu, sesi sebelumnya saya akhiri.\n\n"
            "Sesi telah berakhir."
        )
        response.message = f"{notice}\n\n{response.message}"
        response.messages = [notice, *(response.messages or [response.message.removeprefix(f"{notice}\n\n")])]
        response.metadata = {**response.metadata, "session_timeout": True}
        return response

    @staticmethod
    def intro_message() -> str:
        return (
            "Halo, saya Marawa BPS Padang Pariaman.\n"
            "Saya siap membantu layanan berikut:\n\n"
            "1. Mencari data statistik BPS Kabupaten Padang Pariaman\n"
            "2. Rekomendasi dan konsultasi statistik\n"
            "3. Menghubungkan Anda dengan admin\n"
            "4. Mengakhiri percakapan\n\n"
            "Silakan ketik nomor menu atau tuliskan kebutuhan Anda."
        )

    @classmethod
    def admin_finished_user_message(cls) -> str:
        return (
            "Admin telah menyelesaikan sesi bantuan.\n\n"
            "Bot Marawa sudah aktif kembali untuk percakapan ini.\n\n"
            f"{cls.intro_message()}\n\n"
            f"{cls.main_menu('Silakan pilih layanan yang dibutuhkan.')}"
        )

    async def _show_data_options(
        self,
        session: Session,
        text: str,
        keywords: list[str] | None = None,
        years: list[str] | None = None,
        periods: list[str] | None = None,
    ) -> BotResponse:
        query = await self.ai_client.extract_data_query(text)
        if self._is_generic_data_request(query or text):
            session.state = SessionState.ASKING_DATA_QUERY
            return BotResponse(self._with_submenu_navigation("Boleh. Data apa yang ingin dicari?"), Intent.DATA_REQUEST)
        keyword_candidates = await self._data_keyword_candidates(text, query, keywords)
        result = await self.bps_client.search_variable_options(
            query,
            keywords=keyword_candidates,
            learned_choices=session.learned_data_choices,
        )
        if not result.found:
            session.state = SessionState.ASKING_DATA_QUERY
            if self._has_source_error(result.metadata):
                return self._data_response(
                    f"{result.summary}\n\nSilakan coba lagi beberapa saat lagi, atau ketik menu untuk kembali ke menu utama.",
                    source_url=CONSULTATION_LINK,
                    metadata=result.metadata or {},
                )
            return self._data_response(
                "Maaf, saya belum menemukan data yang sesuai.\n\n"
                "Silakan tuliskan kata kunci yang lebih spesifik, atau ketik menu untuk kembali ke menu utama.",
                source_url=CONSULTATION_LINK,
                metadata=result.metadata or {},
            )
        session.pending_data_query = query
        session.pending_data_keywords = keyword_candidates
        session.pending_bps_matches = result.metadata.get("matches", []) if result.metadata else []
        session.pending_bps_source_groups = result.metadata.get("source_groups", {}) if result.metadata else {}
        if not session.pending_bps_matches:
            session.state = SessionState.MAIN_MENU
            return self._data_response(result.summary, result.source_url, result.metadata or {}, include_menu=True)
        session.pending_bps_options_page = 0
        session.pending_bps_source_pages = {source: 0 for source in SOURCE_ORDER}
        session.pending_bps_visible_choices = []
        session.pending_bps_active_source = ""
        session.pending_bps_periods = []
        session.pending_data_years = years or []
        session.pending_data_periods = periods or []
        session.selected_bps_variable = None
        session.state = SessionState.CONFIRMING_DATA_VARIABLE
        response = self._data_options_response(session, result.metadata or {})
        response.source_url = result.source_url
        return response

    async def _confirm_data_variable(self, session: Session, text: str) -> BotResponse:
        if self._is_next_options_request(text):
            return self._show_next_data_options(session, text)
        if self._is_previous_options_request(text):
            return self._show_previous_data_options(session, text)
        selected = self.bps_client.pick_candidate(text, session.pending_bps_visible_choices or session.pending_bps_matches)
        if not selected:
            decision = await self.agent.plan(session, text)
            guarded_response = self._guarded_agent_response(decision)
            if guarded_response:
                # Add navigation hint so user knows how to escape this state
                guarded_response.message += f"\n\n{self._submenu_navigation_text()}"
                return guarded_response
            return await self._show_data_options(
                session,
                decision.query or text,
                keywords=decision.keywords,
                years=decision.years,
                periods=decision.periods,
            )
        session.selected_bps_variable = selected
        self._remember_learned_choice(session, selected)
        if self._is_publication_candidate(selected):
            return await self._answer_selected_publication(session)
        session.pending_bps_periods = await self.bps_client.get_period_options(selected)
        session.state = SessionState.ASKING_DATA_YEAR
        title = selected.get("title") or "data tersebut"
        if session.pending_data_years and (
            not self.bps_client.has_quarterly_periods(session.pending_bps_periods) or session.pending_data_periods
        ):
            return await self._answer_data_table(
                session,
                self._period_text(session.pending_data_periods) or " ".join(session.pending_data_years),
            )
        if self.bps_client.has_quarterly_periods(session.pending_bps_periods):
            return BotResponse(
                f"Baik, saya pakai data berikut:\n\n{title}\n\n"
                "Tahun dan triwulan berapa yang dibutuhkan?\n\n"
                "Contoh:\n"
                "- 2024 triwulan 1\n"
                "- 2024 TW 1-4\n"
                "- 2023-2024 triwulan 4\n\n"
                f"{self._submenu_navigation_text()}",
                Intent.DATA_REQUEST,
                metadata={"selected_variable": selected, "periods": session.pending_bps_periods},
            )
        return self._data_response(
            f"Baik, saya pakai data berikut:\n\n{title}\n\n"
            "Tahun berapa yang dibutuhkan?\n\n"
            "Contoh: 2023 atau 2023-2025.\n\n"
            f"{self._submenu_navigation_text()}",
            metadata={"selected_variable": selected},
        )

    async def _answer_selected_publication(self, session: Session) -> BotResponse:
        result = await self.bps_client.fetch_table_by_variable(
            session.pending_data_query or "",
            session.selected_bps_variable or {},
            [],
            periods=[],
        )
        if not result.found:
            session.state = SessionState.CONFIRMING_DATA_VARIABLE
            return self._data_response(
                "Maaf, detail publikasi yang dipilih belum bisa saya ambil.\n\n"
                "Silakan pilih publikasi lain, tuliskan kata kunci yang lebih spesifik, atau ketik menu untuk kembali ke menu utama.",
                result.source_url,
                result.metadata or {},
            )
        self._remember_last_data_context(session, result)
        self._clear_data_context(session)
        session.state = SessionState.MAIN_MENU
        return self._data_response(result.message, result.source_url, result.metadata or {}, include_menu=True)

    async def _answer_data_table(self, session: Session, text: str) -> BotResponse:
        years = parse_years(text) or session.pending_data_years
        periods = parse_quarter_periods(text)
        if not years:
            return self._data_response(
                "Mohon ketik tahun yang dibutuhkan.\n\n"
                "Contoh: 2023 atau 2023-2025.\n\n"
                f"{self._submenu_navigation_text()}"
            )
        if self.bps_client.has_quarterly_periods(session.pending_bps_periods) and not periods:
            session.pending_data_years = years
            return self._data_response(
                "Data ini tersedia per triwulan.\n\n"
                "Mohon ketik triwulan yang dibutuhkan.\n\n"
                "Contoh: triwulan 1, TW 1-4, atau semua triwulan.\n\n"
                f"{self._submenu_navigation_text()}"
            )
        if not session.selected_bps_variable:
            self._clear_data_context(session)
            session.state = SessionState.ASKING_DATA_QUERY
            return self._data_response(self._with_submenu_navigation("Boleh. Data apa yang ingin dicari?"))
        result = await self.bps_client.fetch_table_by_variable(
            session.pending_data_query or "",
            session.selected_bps_variable,
            years,
            periods=periods,
        )
        if not result.found:
            session.state = SessionState.ASKING_DATA_YEAR if session.selected_bps_variable else SessionState.ASKING_DATA_QUERY
            session.pending_data_years = years
            return self._data_response(
                self._format_recoverable_data_error(result.message, session),
                result.source_url,
                result.metadata or {},
            )
        self._remember_last_data_context(session, result)
        self._clear_data_context(session)
        session.state = SessionState.MAIN_MENU
        return self._data_response(result.message, result.source_url, result.metadata or {}, include_menu=True)

    def _clear_data_context(self, session: Session) -> None:
        session.pending_data_query = None
        session.pending_data_keywords = []
        session.pending_bps_matches = []
        session.pending_bps_options_page = 0
        session.pending_bps_source_groups = {}
        session.pending_bps_source_pages = {}
        session.pending_bps_visible_choices = []
        session.pending_bps_active_source = ""
        session.pending_bps_periods = []
        session.pending_data_years = []
        session.pending_data_periods = []
        session.selected_bps_variable = None

    def _format_recoverable_data_error(self, message: str, session: Session) -> str:
        if session.state == SessionState.ASKING_DATA_YEAR:
            return (
                f"{message}\n\n"
                "Silakan ketik tahun atau rentang tahun lain yang tersedia.\n"
                "Ketik menu jika ingin kembali ke menu utama."
            )
        return (
            f"{message}\n\n"
            "Silakan tuliskan kata kunci data yang lain.\n"
            "Ketik menu jika ingin kembali ke menu utama."
        )

    def _has_source_error(self, metadata: dict | None) -> bool:
        if not metadata:
            return False
        return any(key in metadata for key in ("error", "simdasi_error", "publication_error"))

    def _intent_from_parsed(self, parsed: dict) -> Intent:
        try:
            return Intent(parsed.get("intent", "ambiguous"))
        except ValueError:
            return Intent.AMBIGUOUS

    def _period_text(self, periods: list[str]) -> str:
        if not periods:
            return ""
        return " ".join(periods)

    def _is_generic_data_request(self, text: str) -> bool:
        normalized = " ".join(str(text or "").lower().strip().split())
        return normalized in {
            "",
            "data",
            "minta data",
            "cari data",
            "carikan data",
            "butuh data",
            "mau data",
            "ingin data",
            "permintaan data",
        }

    def _remember_last_data_context(self, session: Session, result) -> None:
        metadata = result.metadata or {}
        variable = metadata.get("variable") or session.selected_bps_variable or {}
        session.last_data_context = {
            "query": metadata.get("query") or session.pending_data_query,
            "variable": variable,
            "title": variable.get("title") or variable.get("label") or variable.get("name"),
            "requested_years": metadata.get("requested_years") or metadata.get("years") or [],
            "displayed_years": metadata.get("displayed_years") or [],
            "missing_years": metadata.get("missing_years") or [],
            "periods": metadata.get("periods") or [],
        }

    async def _data_keyword_candidates(self, text: str, query: str, keywords: list[str] | None) -> list[str]:
        candidates = [query, *(keywords or [])]
        if hasattr(self.ai_client, "extract_data_keywords"):
            candidates.extend(await self.ai_client.extract_data_keywords(text))
        cleaned = []
        for item in candidates:
            value = str(item).strip().lower()
            if value and value not in cleaned:
                cleaned.append(value)
        return cleaned[:12]

    def _remember_learned_choice(self, session: Session, selected: dict) -> None:
        keys = [session.pending_data_query or "", *session.pending_data_keywords]
        for key in keys:
            normalized = self._normalize_learning_key(key)
            if normalized:
                session.learned_data_choices[normalized] = dict(selected)

    def _normalize_learning_key(self, text: str) -> str:
        return " ".join(str(text).lower().replace("+", " ").split())

    def _looks_like_data_followup(self, text: str, session: Session) -> bool:
        if not session.last_data_context:
            return False
        normalized = text.lower()
        # Only treat as followup if text explicitly references missing/absent data
        followup_words = {"mana", "kok", "loh", "kenapa", "tidak ada", "ga ada", "gak ada", "belum ada"}
        has_followup_word = any(word in normalized for word in followup_words)
        if not has_followup_word:
            return False
        # Additionally check if years are mentioned together with followup words
        has_year = bool(parse_years(text))
        missing_years = session.last_data_context.get("missing_years") or []
        return has_followup_word and (has_year or bool(missing_years))

    def _answer_last_data_followup(self, text: str, session: Session) -> str:
        context = session.last_data_context
        title = context.get("title") or "data terakhir"
        asked_years = parse_years(text)
        missing_years = context.get("missing_years") or []
        displayed_years = context.get("displayed_years") or []
        requested = asked_years or missing_years
        if requested:
            missing_requested = [year for year in requested if year in missing_years]
            if missing_requested:
                return (
                    f"Untuk data {title}, tahun {', '.join(missing_requested)} belum tersedia di WebAPI BPS.\n\n"
                    f"Tahun yang berhasil ditampilkan tadi: {', '.join(displayed_years) if displayed_years else '-'}.\n\n"
                    "Silakan ketik data lain, atau ketik menu untuk kembali ke menu utama."
                )
        if missing_years:
            return (
                f"Untuk data {title}, sebagian tahun belum tersedia: {', '.join(missing_years)}.\n\n"
                f"Tahun yang berhasil ditampilkan tadi: {', '.join(displayed_years) if displayed_years else '-'}.\n\n"
                "Silakan ketik data lain, atau ketik menu untuk kembali ke menu utama."
            )
        return (
            f"Konteks terakhir adalah data {title}.\n\n"
            "Kalau ingin mencari data lain, langsung ketik kata kuncinya.\n"
            "Kalau ingin kembali ke menu utama, ketik menu."
        )

    def _format_data_options(self, session: Session) -> tuple[str, str]:
        from app.services.wa_formatter import format_data_options
        source_groups = session.pending_bps_source_groups or {"dynamic_table": session.pending_bps_matches}
        source_pages = session.pending_bps_source_pages or {source: session.pending_bps_options_page for source in SOURCE_ORDER}

        options_message, visible_choices, guidance_message = format_data_options(
            source_groups=source_groups,
            source_pages=source_pages,
            page_size=DATA_OPTION_PAGE_SIZE,
            source_order=SOURCE_ORDER,
        )
        session.pending_bps_visible_choices = visible_choices
        return options_message, guidance_message

    def _data_options_response(self, session: Session, metadata: dict | None = None) -> BotResponse:
        options_message, guidance_message = self._format_data_options(session)
        messages = [options_message, guidance_message, self._data_help_footer()]
        return BotResponse(
            "\n\n".join(messages),
            Intent.DATA_REQUEST,
            metadata=metadata or {},
            messages=messages,
        )

    @staticmethod
    def _format_option_title(item: dict) -> str:
        title = str(item.get("title") or item.get("label") or item.get("name") or "-").strip()
        source_label = str(item.get("source_label") or "Tabel Dinamis").strip()
        if not source_label or title.startswith("["):
            return title
        return f"[{source_label}] {title}"

    @staticmethod
    def _is_publication_candidate(item: dict) -> bool:
        return str(item.get("source_type") or "").lower() == "publication"

    def _show_next_data_options(self, session: Session, text: str = "") -> BotResponse:
        source = self._requested_source(text) or self._next_available_source(session)
        if not source:
            return BotResponse(
                "Pilihan berikutnya belum tersedia.\n\n"
                "Silakan pilih nomor yang ada, atau tuliskan kata kunci yang lebih detail.\n\n"
                f"{self._submenu_navigation_text()}",
                Intent.DATA_REQUEST,
            )
        session.pending_bps_source_pages[source] = session.pending_bps_source_pages.get(source, 0) + 1
        session.pending_bps_active_source = source
        return self._data_options_response(session, {"matches": session.pending_bps_matches, "source": source})

    def _show_previous_data_options(self, session: Session, text: str = "") -> BotResponse:
        source = self._requested_source(text) or session.pending_bps_active_source
        if not source or session.pending_bps_source_pages.get(source, 0) <= 0:
            return BotResponse(
                "Ini sudah halaman pertama.\n\n"
                "Silakan pilih nomor yang ada, atau tuliskan kata kunci yang lebih detail.\n\n"
                f"{self._submenu_navigation_text()}",
                Intent.DATA_REQUEST,
            )
        session.pending_bps_source_pages[source] -= 1
        session.pending_bps_active_source = source
        return self._data_options_response(session, {"matches": session.pending_bps_matches, "source": source})

    def _has_next_source_page(self, session: Session) -> bool:
        groups = session.pending_bps_source_groups or {"dynamic_table": session.pending_bps_matches}
        pages = session.pending_bps_source_pages or {}
        return any((pages.get(source, 0) + 1) * DATA_OPTION_PAGE_SIZE < len(groups.get(source, [])) for source in SOURCE_ORDER)

    def _has_previous_source_page(self, session: Session) -> bool:
        return any(page > 0 for page in (session.pending_bps_source_pages or {}).values())

    def _next_available_source(self, session: Session) -> str:
        groups = session.pending_bps_source_groups or {"dynamic_table": session.pending_bps_matches}
        pages = session.pending_bps_source_pages or {}
        start_index = SOURCE_ORDER.index(session.pending_bps_active_source) if session.pending_bps_active_source in SOURCE_ORDER else 0
        ordered = [*SOURCE_ORDER[start_index:], *SOURCE_ORDER[:start_index]]
        for source in ordered:
            if (pages.get(source, 0) + 1) * DATA_OPTION_PAGE_SIZE < len(groups.get(source, [])):
                return source
        return ""

    def _requested_source(self, text: str) -> str:
        normalized = " ".join(text.lower().strip().split())
        if any(word in normalized for word in {"publikasi", "publication"}):
            return "publication"
        if "simdasi" in normalized:
            return "simdasi"
        if "dynamic" in normalized or "tabel dinamis" in normalized or "dinamis" in normalized:
            return "dynamic_table"
        return ""

    def _is_next_options_request(self, text: str) -> bool:
        normalized = text.strip().lower()
        return normalized in {"lainnya", "lanjut", "next", "berikutnya", "hasil berikutnya"} or normalized.startswith("lainnya ")

    def _is_previous_options_request(self, text: str) -> bool:
        normalized = text.strip().lower()
        return normalized in {"sebelumnya", "prev", "previous", "kembali"} or normalized.startswith("sebelumnya ")

    def _is_greeting(self, lowered: str) -> bool:
        normalized = " ".join(lowered.strip().split())
        return normalized in {
            "halo",
            "hallo",
            "hello",
            "hai",
            "hi",
            "pagi",
            "siang",
            "sore",
            "malam",
            "selamat pagi",
            "selamat siang",
            "selamat sore",
            "selamat malam",
            "assalamualaikum",
            "assalamu alaikum",
        }

    @staticmethod
    def main_menu(prefix: str = "Halo, saya Marawa BPS Padang Pariaman.") -> str:
        return (
            f"{prefix}\n\n"
            "1. Mencari data statistik BPS Kabupaten Padang Pariaman\n"
            "2. Rekomendasi dan konsultasi statistik\n"
            "3. Menghubungkan Anda dengan admin\n"
            "4. Mengakhiri percakapan\n\n"
            "Silakan ketik nomor menu atau tuliskan kebutuhan Anda."
        )
