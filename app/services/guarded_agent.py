from dataclasses import dataclass, field
import logging
import time
from typing import Any

from app.conversation.parsing import parse_years
from app.models import Intent, Session, SessionState
from app.services.ai_client import AiClient


ALLOWED_AGENT_ACTIONS = {
    "ask_data_query",
    "search_bps_variables",
    "show_consultation",
    "handoff_admin",
    "exit",
    "show_menu",
    "clarify",
    "reject_unsafe",
    "reject_out_of_scope",
}

logger = logging.getLogger(__name__)


@dataclass
class AgentDecision:
    action: str
    intent: str
    query: str = ""
    keywords: list[str] = field(default_factory=list)
    years: list[str] = field(default_factory=list)
    periods: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class GuardedDataAgent:
    def __init__(self, ai_client: AiClient):
        self.ai_client = ai_client

    async def plan(self, session: Session, text: str) -> AgentDecision:
        started = time.perf_counter()
        context = {
            "state": session.state.value,
            "last_data_context": session.last_data_context,
        }
        try:
            if hasattr(self.ai_client, "parse_service_request"):
                parsed = await self.ai_client.parse_service_request(text, context)
            else:
                intent = await self.ai_client.classify_intent(text, context)
                query = await self.ai_client.extract_data_query(text) if intent == Intent.DATA_REQUEST else ""
                parsed = {"intent": intent.value, "query": query, "years": parse_years(text), "periods": [], "safe": True}
            decision = self._decision_from_parsed(session, parsed)
            if decision.action not in ALLOWED_AGENT_ACTIONS:
                return AgentDecision("clarify", "ambiguous", metadata={"parsed": parsed, "reason": "action_not_allowed"})
            decision.metadata = {**decision.metadata, "ai_ms": (time.perf_counter() - started) * 1000}
            return decision
        finally:
            logger.info("ai.timing ai_ms=%.1f state=%s", (time.perf_counter() - started) * 1000, session.state.value)

    def _decision_from_parsed(self, session: Session, parsed: dict[str, Any]) -> AgentDecision:
        intent = str(parsed.get("intent") or "ambiguous")
        metadata = {"agent": {"parsed": parsed}}
        if parsed.get("safe") is False or intent == "unsafe":
            return AgentDecision("reject_unsafe", "ambiguous", metadata=metadata)
        if intent == "out_of_scope":
            return AgentDecision("reject_out_of_scope", "ambiguous", metadata=metadata)
        if intent == "data_request":
            query = str(parsed.get("query") or "").strip()
            action = "search_bps_variables" if query and len(query) >= 3 else "ask_data_query"
            return AgentDecision(
                action,
                intent,
                query=query,
                keywords=self._string_list(parsed.get("keywords")),
                years=list(parsed.get("years") or []),
                periods=list(parsed.get("periods") or []),
                metadata=metadata,
            )
        if intent == "consultation":
            return AgentDecision("show_consultation", intent, metadata=metadata)
        if intent == "admin":
            return AgentDecision("handoff_admin", intent, metadata=metadata)
        if intent == "exit":
            return AgentDecision("exit", intent, metadata=metadata)
        if intent == "menu":
            return AgentDecision("show_menu", intent, metadata=metadata)
        if session.state == SessionState.ASKING_DATA_QUERY:
            return AgentDecision(
                "search_bps_variables",
                "data_request",
                query=str(parsed.get("query") or "").strip(),
                keywords=self._string_list(parsed.get("keywords")),
                metadata=metadata,
            )
        return AgentDecision("clarify", "ambiguous", metadata=metadata)

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()][:12]
