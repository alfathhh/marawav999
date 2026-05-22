from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bps_api_key: str = Field(default="", alias="BPS_API_KEY")
    bps_domain: str = Field(default="1306", alias="BPS_DOMAIN")
    bps_cache_ttl_seconds: int = Field(default=3600, alias="BPS_CACHE_TTL_SECONDS")
    bps_cache_db_path: str = Field(default="/app/data/bps_cache.sqlite3", alias="BPS_CACHE_DB_PATH")

    ai_provider: str = Field(default="openai", alias="AI_PROVIDER")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    ollama_base_url: str = Field(default="http://host.docker.internal:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="llama3.1", alias="OLLAMA_MODEL")

    gowa_base_url: str = Field(default="http://gowa:3000", alias="GOWA_BASE_URL")
    gowa_basic_auth_user: str = Field(default="", alias="GOWA_BASIC_AUTH_USER")
    gowa_basic_auth_pass: str = Field(default="", alias="GOWA_BASIC_AUTH_PASS")
    gowa_webhook_secret: str = Field(default="secret", alias="GOWA_WEBHOOK_SECRET")

    admin_numbers: str = Field(default="", alias="ADMIN_NUMBERS")
    google_sheets_spreadsheet_id: str = Field(default="", alias="GOOGLE_SHEETS_SPREADSHEET_ID")
    google_service_account_json: str = Field(default="", alias="GOOGLE_SERVICE_ACCOUNT_JSON")

    session_timeout_seconds: int = Field(default=600, alias="SESSION_TIMEOUT_SECONDS")
    admin_pickup_timeout_seconds: int = Field(default=300, alias="ADMIN_PICKUP_TIMEOUT_SECONDS")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def admin_number_list(self) -> list[str]:
        return [number.strip() for number in self.admin_numbers.split(",") if number.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
