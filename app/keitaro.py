from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx

from app.config import Settings


class KeitaroError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class KeitaroClient:
    """Small Admin API client. Error messages intentionally omit response bodies and headers."""

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        if not settings.keitaro_base_url or not settings.keitaro_api_key:
            raise KeitaroError("Keitaro не настроен: задайте KEITARO_BASE_URL и KEITARO_API_KEY")
        self._base = settings.keitaro_base_url.rstrip("/") + "/admin_api/v1/"
        self._client = httpx.Client(
            headers={"Api-Key": settings.keitaro_api_key, "Accept": "application/json"},
            timeout=settings.request_timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def request(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                json: dict[str, Any] | list[Any] | None = None) -> Any:
        url = urljoin(self._base, path.lstrip("/"))
        try:
            response = self._client.request(method, url, params=params, json=json)
        except httpx.TimeoutException as exc:
            raise KeitaroError("Keitaro не ответил вовремя; состояние операции нужно проверить в панели") from exc
        except httpx.HTTPError as exc:
            raise KeitaroError("Не удалось связаться с Keitaro API") from exc
        if response.is_error:
            raise KeitaroError(
                f"Keitaro API {method.upper()} {path} вернул HTTP {response.status_code}",
                status_code=response.status_code,
            )
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise KeitaroError(f"Keitaro API {method.upper()} {path} вернул некорректный JSON") from exc

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self.request("POST", path, json=body)

    def put(self, path: str, body: dict[str, Any]) -> Any:
        return self.request("PUT", path, json=body)


def as_list(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    if isinstance(response, dict):
        for key in ("data", "items", "result"):
            value = response.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def find_by_id(items: list[dict[str, Any]], item_id: int | str) -> dict[str, Any] | None:
    return next((item for item in items if str(item.get("id")) == str(item_id)), None)
