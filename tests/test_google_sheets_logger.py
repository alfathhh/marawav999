import pytest

from app.services.google_sheets_logger import GoogleSheetsLogger, MAX_SHEETS_CELL_CHARS, MAX_SHEETS_METADATA_CHARS


@pytest.mark.asyncio
async def test_google_sheets_logger_disabled_is_graceful():
    logger = GoogleSheetsLogger()

    await logger.log_user("6281", "Tester", 1, "MAIN_MENU")
    await logger.log_conversation("6281", "Tester", "in", "MAIN_MENU", "data_request", "halo")

    assert not logger.enabled()


def test_google_sheets_logger_loads_service_account_from_file(tmp_path):
    credential = {
        "type": "service_account",
        "project_id": "test",
        "private_key_id": "id",
        "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
        "client_email": "bot@example.test",
        "client_id": "1",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    path = tmp_path / "service-account.json"
    path.write_text(__import__("json").dumps(credential), encoding="utf-8")
    logger = GoogleSheetsLogger("spreadsheet", str(path))

    assert logger._load_service_account_info()["client_email"] == "bot@example.test"


def test_google_sheets_logger_truncates_large_cells():
    logger = GoogleSheetsLogger("spreadsheet", "{}")

    cell = logger._cell("A" * 60000)
    metadata = logger._metadata_cell({"large": "B" * 60000})

    assert len(cell) <= MAX_SHEETS_CELL_CHARS
    assert "Log dipotong" in cell
    assert len(metadata) <= MAX_SHEETS_METADATA_CHARS
    assert "Log dipotong" in metadata
