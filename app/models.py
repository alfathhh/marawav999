from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class Intent(StrEnum):
    DATA_REQUEST = "data_request"
    CONSULTATION = "consultation"
    ADMIN = "admin"
    EXIT = "exit"
    MENU = "menu"
    CANCEL = "cancel"
    AMBIGUOUS = "ambiguous"


class SessionState(StrEnum):
    MAIN_MENU = "MAIN_MENU"
    ASKING_DATA_QUERY = "ASKING_DATA_QUERY"
    CONFIRMING_DATA_VARIABLE = "CONFIRMING_DATA_VARIABLE"
    ASKING_DATA_YEAR = "ASKING_DATA_YEAR"
    WAITING_ADMIN = "WAITING_ADMIN"
    TALKING_TO_ADMIN = "TALKING_TO_ADMIN"
    ENDED = "ENDED"


@dataclass
class UserMessage:
    phone: str
    name: str
    text: str


@dataclass
class BotResponse:
    message: str
    intent: Intent | None = None
    source_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    should_send: bool = True
    messages: list[str] = field(default_factory=list)


@dataclass
class Session:
    phone: str
    name: str = ""
    state: SessionState = SessionState.MAIN_MENU
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    total_sessions: int = 1
    needs_intro: bool = True
    timeout_notice_pending: bool = False
    history: list[dict[str, str]] = field(default_factory=list)
    handoff_started_at: datetime | None = None
    pending_data_query: str | None = None
    pending_data_keywords: list[str] = field(default_factory=list)
    pending_data_years: list[str] = field(default_factory=list)
    pending_bps_matches: list[dict[str, Any]] = field(default_factory=list)
    pending_bps_options_page: int = 0
    pending_bps_source_groups: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    pending_bps_source_pages: dict[str, int] = field(default_factory=dict)
    pending_bps_visible_choices: list[dict[str, Any]] = field(default_factory=list)
    pending_bps_active_source: str = ""
    pending_bps_periods: list[dict[str, Any]] = field(default_factory=list)
    pending_data_periods: list[str] = field(default_factory=list)
    selected_bps_variable: dict[str, Any] | None = None
    last_data_context: dict[str, Any] = field(default_factory=dict)
    learned_data_choices: dict[str, dict[str, Any]] = field(default_factory=dict)
