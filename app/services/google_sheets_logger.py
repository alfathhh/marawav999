import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build


logger = logging.getLogger(__name__)
MAX_SHEETS_CELL_CHARS = 49000
MAX_SHEETS_METADATA_CHARS = 20000


class GoogleSheetsLogger:
    USER_HEADERS = ["nomor", "nama_whatsapp", "pertama_interaksi", "terakhir_aktif", "total_sesi", "status_terakhir"]
    CONVERSATION_HEADERS = ["timestamp", "nomor", "nama", "arah", "state", "intent", "isi_pesan", "respons_bot", "metadata", "url_sumber"]

    def __init__(self, spreadsheet_id: str = "", service_account_json: str = ""):
        self.spreadsheet_id = spreadsheet_id
        self.service_account_json = service_account_json
        self._service = None
        self._schema_ready = False

    def enabled(self) -> bool:
        return bool(self.spreadsheet_id and self.service_account_json)

    async def log_user(self, phone: str, name: str, total_sessions: int, status: str) -> None:
        if not self.enabled():
            return
        try:
            service = self._get_service()
            self._ensure_schema(service)
            now = datetime.now(timezone.utc).isoformat()
            rows = service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range="users!A:F",
            ).execute().get("values", [])
            row_index = next((index for index, row in enumerate(rows, start=1) if row and row[0] == phone), None)
            if row_index:
                first_seen = rows[row_index - 1][2] if len(rows[row_index - 1]) > 2 else now
                values = [[phone, name, first_seen, now, total_sessions, status]]
                service.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"users!A{row_index}:F{row_index}",
                    valueInputOption="USER_ENTERED",
                    body={"values": values},
                ).execute()
            else:
                values = [[phone, name, now, now, total_sessions, status]]
                service.spreadsheets().values().append(
                    spreadsheetId=self.spreadsheet_id,
                    range="users!A:F",
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": values},
                ).execute()
        except Exception as exc:
            logger.exception("google_sheets.log_user_failed phone=%s error=%s", phone, exc)
            return

    async def log_conversation(
        self,
        phone: str,
        name: str,
        direction: str,
        state: str,
        intent: str,
        message: str,
        bot_response: str = "",
        metadata: dict[str, Any] | None = None,
        source_url: str | None = None,
    ) -> None:
        if not self.enabled():
            return
        try:
            service = self._get_service()
            self._ensure_schema(service)
            values = [[
                datetime.now(timezone.utc).isoformat(),
                self._cell(phone),
                self._cell(name),
                self._cell(direction),
                self._cell(state),
                self._cell(intent),
                self._cell(message),
                self._cell(bot_response),
                self._metadata_cell(metadata or {}),
                self._cell(source_url or ""),
            ]]
            service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range="conversations!A:J",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": values},
            ).execute()
        except Exception as exc:
            logger.exception("google_sheets.log_conversation_failed phone=%s direction=%s error=%s", phone, direction, exc)
            return

    def _get_service(self):
        if self._service:
            return self._service
        info = self._load_service_account_info()
        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        self._service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        return self._service

    def _load_service_account_info(self) -> dict[str, Any]:
        value = self.service_account_json.strip()
        if value.startswith("{"):
            return json.loads(value)
        path = Path(value)
        if not path.exists():
            raise FileNotFoundError(f"Google service account file tidak ditemukan: {value}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _ensure_schema(self, service) -> None:
        if self._schema_ready:
            return
        spreadsheet = service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
        titles = {sheet.get("properties", {}).get("title") for sheet in spreadsheet.get("sheets", [])}
        requests = []
        for title in ("users", "conversations"):
            if title not in titles:
                requests.append({"addSheet": {"properties": {"title": title}}})
        if requests:
            service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": requests},
            ).execute()
        self._ensure_headers(service, "users", self.USER_HEADERS)
        self._ensure_headers(service, "conversations", self.CONVERSATION_HEADERS)
        self._schema_ready = True

    def _ensure_headers(self, service, sheet_name: str, headers: list[str]) -> None:
        row = service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"{sheet_name}!1:1",
        ).execute().get("values", [])
        if row:
            return
        end_column = chr(ord("A") + len(headers) - 1)
        service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{sheet_name}!A1:{end_column}1",
            valueInputOption="USER_ENTERED",
            body={"values": [headers]},
        ).execute()

    def _cell(self, value: Any, limit: int = MAX_SHEETS_CELL_CHARS) -> str:
        text = str(value or "")
        if len(text) <= limit:
            return text
        suffix = f"\n\n[Log dipotong: {len(text) - limit} karakter tidak disimpan di Google Sheets.]"
        return text[: max(0, limit - len(suffix))] + suffix

    def _metadata_cell(self, metadata: dict[str, Any]) -> str:
        try:
            text = json.dumps(metadata, ensure_ascii=False)
        except TypeError:
            text = json.dumps({"unserializable_metadata": str(metadata)}, ensure_ascii=False)
        return self._cell(text, MAX_SHEETS_METADATA_CHARS)
