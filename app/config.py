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

    @property
    def keitaro_configured(self) -> bool:
        placeholder = self.keitaro_api_key.lower().startswith("replace-with")
        return bool(self.keitaro_base_url and self.keitaro_api_key and not placeholder)


def get_settings() -> Settings:
    load_dotenv(Path.cwd() / ".env", override=False)
    base_url = os.getenv("KEITARO_BASE_URL", "").strip().rstrip("/")
    if base_url.endswith("/admin_api/v1"):
        base_url = base_url[: -len("/admin_api/v1")]
    return Settings(
        keitaro_base_url=base_url,
        keitaro_api_key=os.getenv("KEITARO_API_KEY", "").strip(),
        database_url=os.getenv(
            "DATABASE_URL", "sqlite:///./data/keitaro_wrapper.sqlite3"
        ).strip(),
        campaign_prefix=os.getenv("CAMPAIGN_PREFIX", "test_ryzhkov_").strip(),
        redirect_action=os.getenv("KEITARO_REDIRECT_ACTION", "").strip(),
    )
