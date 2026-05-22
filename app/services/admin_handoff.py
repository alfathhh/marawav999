from datetime import datetime, timezone

from app.models import Session, SessionState
from app.services.gowa_client import GowaClient


class AdminHandoffService:
    def __init__(self, gowa_client: GowaClient, admin_numbers: list[str], pickup_timeout_seconds: int = 300):
        self.gowa_client = gowa_client
        self.admin_numbers = admin_numbers
        self.pickup_timeout_seconds = pickup_timeout_seconds

    async def start(self, session: Session) -> None:
        session.state = SessionState.WAITING_ADMIN
        session.handoff_started_at = datetime.now(timezone.utc)
        summary = self._summary(session)
        for admin in self.admin_numbers:
            await self.gowa_client.send_text(admin, summary)

    def is_pickup_expired(self, session: Session) -> bool:
        if not session.handoff_started_at:
            return False
        return (datetime.now(timezone.utc) - session.handoff_started_at).total_seconds() > self.pickup_timeout_seconds

    def is_admin_command(self, text: str, sender: str) -> bool:
        return sender in self.admin_numbers and text.strip().lower().split(maxsplit=1)[0:1] == ["selesai"]

    def parse_finished_user(self, text: str) -> str:
        parts = text.strip().split(maxsplit=1)
        return parts[1].strip() if len(parts) == 2 else ""

    def _summary(self, session: Session) -> str:
        history = "\n".join(f"- {item.get('user', '')}" for item in session.history)
        return (
            "Permintaan bicara admin Marawa BPS.\n"
            "\n"
            f"Nomor user: {session.phone}\n"
            f"Nama: {session.name or '-'}\n"
            "\n"
            "Ringkasan percakapan:\n"
            f"{history or '-'}\n\n"
            "Bot dimatikan sementara untuk user ini.\n\n"
            "Setelah admin selesai melayani, balas ke nomor bot dengan format:\n"
            f"selesai {session.phone}"
        )
