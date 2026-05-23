from datetime import datetime, timezone

from app.models import Session, SessionState


class SessionStore:
    def __init__(self, timeout_seconds: int = 600, admin_handoff_timeout_seconds: int = 1800):
        self.timeout_seconds = timeout_seconds
        self.admin_handoff_timeout_seconds = admin_handoff_timeout_seconds
        self._sessions: dict[str, Session] = {}

    def get(self, phone: str, name: str = "") -> Session:
        existing = self._sessions.get(phone)
        now = datetime.now(timezone.utc)
        if existing is None:
            session = Session(phone=phone, name=name)
            self._sessions[phone] = session
            return session
        # Start a new session if previous one ended or timed out
        if existing.state == SessionState.ENDED or self._is_interactive_expired(existing, now):
            session = Session(
                phone=phone,
                name=name or existing.name,
                total_sessions=existing.total_sessions + 1,
            )
            self._sessions[phone] = session
            return session
        if name:
            existing.name = name
        return existing

    def expired_interactive_sessions(self, now: datetime | None = None) -> list[Session]:
        now = now or datetime.now(timezone.utc)
        return [
            session
            for session in self._sessions.values()
            if session.state not in {SessionState.ENDED, SessionState.WAITING_ADMIN}
            and self._is_interactive_expired(session, now)
        ]

    def expired_admin_handoff_sessions(self, now: datetime | None = None) -> list[Session]:
        """Return sessions stuck in WAITING_ADMIN past the admin handoff timeout."""
        now = now or datetime.now(timezone.utc)
        results = []
        for session in self._sessions.values():
            if session.state != SessionState.WAITING_ADMIN:
                continue
            if not session.handoff_started_at:
                continue
            elapsed = (now - session.handoff_started_at).total_seconds()
            if elapsed > self.admin_handoff_timeout_seconds:
                results.append(session)
        return results

    def mark_timed_out(self, session: Session) -> Session:
        session.state = SessionState.ENDED
        session.handoff_started_at = None
        session.needs_intro = True
        session.timeout_notice_pending = False
        session.updated_at = datetime.now(timezone.utc)
        self._sessions[session.phone] = session
        return session

    def update(self, session: Session) -> Session:
        session.updated_at = datetime.now(timezone.utc)
        self._sessions[session.phone] = session
        return session

    def end(self, phone: str) -> None:
        session = self._sessions.get(phone)
        if session:
            session.state = SessionState.ENDED
            session.updated_at = datetime.now(timezone.utc)

    def activate(self, phone: str) -> Session:
        session = self._sessions.get(phone) or Session(phone=phone)
        session.state = SessionState.MAIN_MENU
        session.handoff_started_at = None
        session.updated_at = datetime.now(timezone.utc)
        self._sessions[phone] = session
        return session

    def is_expired(self, session: Session, now: datetime | None = None) -> bool:
        if session.state == SessionState.ENDED:
            return False
        if session.state == SessionState.WAITING_ADMIN:
            return self._is_admin_handoff_expired(session, now)
        return self._is_interactive_expired(session, now)

    def _is_interactive_expired(self, session: Session, now: datetime | None = None) -> bool:
        if session.state in {SessionState.ENDED, SessionState.WAITING_ADMIN}:
            return False
        now = now or datetime.now(timezone.utc)
        return (now - session.updated_at).total_seconds() > self.timeout_seconds

    def _is_admin_handoff_expired(self, session: Session, now: datetime | None = None) -> bool:
        if not session.handoff_started_at:
            return False
        now = now or datetime.now(timezone.utc)
        return (now - session.handoff_started_at).total_seconds() > self.admin_handoff_timeout_seconds
