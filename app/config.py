from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    keitaro_base_url: str
    keitaro_api_key: str
    database_url: str
    campaign_prefix: str
    redirect_action: str
    request_timeout_seconds: float = 20.0
    app_username: str = ""
    app_password: str = ""
    app_session_secret: str = ""
    app_cookie_secure: bool = False
    app_session_ttl_seconds: int = 28800

    @property
    def keitaro_configured(self) -> bool:
        placeholder = self.keitaro_api_key.lower().startswith("replace-with")
        return bool(self.keitaro_base_url and self.keitaro_api_key and not placeholder)

    @property
    def app_auth_configured(self) -> bool:
        return (
            bool(self.app_username.strip())
            and len(self.app_password) >= 16
            and not self.app_password.lower().startswith("replace-with")
            and len(self.app_session_secret.encode("utf-8")) >= 32
            and not self.app_session_secret.lower().startswith("replace-with")
        )

    @property
    def database_is_postgres(self) -> bool:
        return self.database_url.lower().startswith(("postgresql://", "postgresql+"))


def get_settings() -> Settings:
    load_dotenv(Path.cwd() / ".env", override=False)
    base_url = os.getenv("KEITARO_BASE_URL", "").strip().rstrip("/")
    if base_url.endswith("/admin_api/v1"):
        base_url = base_url[: -len("/admin_api/v1")]
    try:
        session_ttl = int(os.getenv("APP_SESSION_TTL_SECONDS", "28800"))
    except ValueError:
        session_ttl = 28800
    session_ttl = min(max(session_ttl, 300), 604800)
    cookie_secure = os.getenv("APP_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes", "on"}
    cookie_secure = cookie_secure or os.getenv("VERCEL", "").strip() == "1"
    database_url = (
        os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or "sqlite:///./data/keitaro_wrapper.sqlite3"
    ).strip()
    if database_url.startswith("postgres://"):
        database_url = "postgresql+psycopg://" + database_url.removeprefix("postgres://")
    elif database_url.startswith("postgresql://"):
        database_url = "postgresql+psycopg://" + database_url.removeprefix("postgresql://")
    return Settings(
        keitaro_base_url=base_url,
        keitaro_api_key=os.getenv("KEITARO_API_KEY", "").strip(),
        database_url=database_url,
        campaign_prefix=os.getenv("CAMPAIGN_PREFIX", "test_ryzhkov_").strip(),
        redirect_action=os.getenv("KEITARO_REDIRECT_ACTION", "").strip(),
        app_username=os.getenv("APP_USERNAME", "").strip(),
        app_password=os.getenv("APP_PASSWORD", ""),
        app_session_secret=os.getenv("APP_SESSION_SECRET", ""),
        app_cookie_secure=cookie_secure,
        app_session_ttl_seconds=session_ttl,
    )
