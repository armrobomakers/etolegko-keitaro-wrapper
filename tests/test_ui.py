from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings
from app.db import Base
from app.main import _mount_static_if_available, app, get_db, get_keitaro
from tests.test_services import FakeKeitaro

AUTH_USERNAME = "test-admin"
AUTH_PASSWORD = "test-only-password-long-enough"
AUTH_SECRET = "test-only-session-secret-with-more-than-32-bytes"
SAME_ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture(autouse=True)
def configured_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_USERNAME", AUTH_USERNAME)
    monkeypatch.setenv("APP_PASSWORD", AUTH_PASSWORD)
    monkeypatch.setenv("APP_SESSION_SECRET", AUTH_SECRET)
    monkeypatch.setenv("APP_SESSION_TTL_SECONDS", "28800")
    monkeypatch.setenv("APP_COOKIE_SECURE", "false")
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)


@pytest.fixture
def authenticated_client():
    with TestClient(app, follow_redirects=False) as client:
        response = client.post(
            "/login",
            json={"username": AUTH_USERNAME, "password": AUTH_PASSWORD},
            headers=SAME_ORIGIN,
        )
        assert response.status_code == 200
        yield client


def test_unauthenticated_application_routes_are_blocked() -> None:
    with TestClient(app, follow_redirects=False) as client:
        page = client.get("/")
        api = client.get("/api/health")
        docs = client.get("/docs")
        login_page = client.get("/login")
        login_css = client.get("/static/login.css")

    assert page.status_code == 303 and page.headers["location"] == "/login"
    assert api.status_code == 401
    assert docs.status_code == 401
    assert login_page.status_code == 200
    assert login_css.status_code == 200


def test_auth_configuration_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PASSWORD", "replace-with-a-real-secret")
    with TestClient(app, follow_redirects=False) as client:
        page = client.get("/login")
        application = client.get("/")
        login = client.post(
            "/login",
            json={"username": AUTH_USERNAME, "password": AUTH_PASSWORD},
            headers=SAME_ORIGIN,
        )

    assert page.status_code == 200 and "Аутентификация ещё не настроена" in page.text
    assert application.status_code == 303 and application.headers["location"].endswith("setup=required")
    assert login.status_code == 503


def test_login_rejects_wrong_credentials_and_foreign_origin() -> None:
    with TestClient(app, follow_redirects=False) as client:
        wrong_password = client.post(
            "/login", json={"username": AUTH_USERNAME, "password": "wrong"}, headers=SAME_ORIGIN
        )
        wrong_origin = client.post(
            "/login",
            json={"username": AUTH_USERNAME, "password": AUTH_PASSWORD},
            headers={"Origin": "https://attacker.example"},
        )
        missing_origin = client.post(
            "/login", json={"username": AUTH_USERNAME, "password": AUTH_PASSWORD}
        )

    assert wrong_password.status_code == 401
    assert wrong_origin.status_code == 403
    assert missing_origin.status_code == 403


def test_login_sets_secure_session_cookie_and_logout_clears_it() -> None:
    with TestClient(app, follow_redirects=False) as client:
        response = client.post(
            "/login",
            json={"username": AUTH_USERNAME, "password": AUTH_PASSWORD},
            headers=SAME_ORIGIN,
        )
        cookie = response.headers["set-cookie"].lower()
        page = client.get("/")
        logout = client.post("/logout", headers=SAME_ORIGIN)
        api_after_logout = client.get("/api/health")

    assert response.status_code == 200
    assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=28800" in cookie
    assert "secure" not in cookie
    assert page.status_code == 200
    assert logout.status_code == 303 and logout.headers["location"] == "/login"
    assert api_after_logout.status_code == 401


def test_vercel_enables_secure_cookie(monkeypatch: pytest.MonkeyPatch, authenticated_client) -> None:
    monkeypatch.setenv("VERCEL", "1")
    authenticated_client.cookies.clear()
    response = authenticated_client.post(
        "/login", json={"username": AUTH_USERNAME, "password": AUTH_PASSWORD}, headers=SAME_ORIGIN
    )
    assert response.status_code == 200
    assert "secure" in response.headers["set-cookie"].lower()


def test_vercel_refuses_ephemeral_sqlite_for_database_operations(
    monkeypatch: pytest.MonkeyPatch, authenticated_client
) -> None:
    monkeypatch.setenv("VERCEL", "1")
    response = authenticated_client.get("/api/operations")
    assert response.status_code == 503
    assert "PostgreSQL" in response.json()["detail"]


def test_home_page_and_static_assets_are_served(authenticated_client) -> None:
    page = authenticated_client.get("/")
    script = authenticated_client.get("/static/app.js")
    css = authenticated_client.get("/static/styles.css")
    editor_css = authenticated_client.get("/static/editor.css")
    assert page.status_code == 200
    assert "Новая кампания" in page.text
    assert "Выйти" in page.text
    assert script.status_code == 200 and "Idempotency-Key" in script.text
    assert css.status_code == 200 and ".offer-table" in css.text
    assert editor_css.status_code == 200 and ".stream-card" in editor_css.text


def test_static_mount_is_optional_when_assets_are_not_in_function_bundle(tmp_path) -> None:
    application = FastAPI()
    missing_directory = tmp_path / "public" / "static"

    _mount_static_if_available(application, missing_directory)

    assert not any(getattr(route, "path", None) == "/static" for route in application.routes)


def test_static_mount_is_created_when_assets_exist(tmp_path) -> None:
    application = FastAPI()
    static_directory = tmp_path / "static"
    static_directory.mkdir()

    _mount_static_if_available(application, static_directory)

    assert any(getattr(route, "path", None) == "/static" for route in application.routes)


def test_health_never_returns_keitaro_secret(authenticated_client) -> None:
    response = authenticated_client.get("/api/health")
    assert response.status_code == 200
    assert "api_key" not in response.text.lower()
    assert "password" not in response.text.lower()


def test_campaign_endpoint_is_idempotent_with_same_key() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    tracker = FakeKeitaro()
    settings = Settings(
        "https://tracker.example", "not-a-real-key", "sqlite:///:memory:", "test_ryzhkov_", "",
        app_username=AUTH_USERNAME, app_password=AUTH_PASSWORD, app_session_secret=AUTH_SECRET,
    )

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_keitaro] = lambda: tracker
    app.dependency_overrides[get_settings] = lambda: settings
    payload = {
        "name": "API test", "country_codes": ["US"], "offer_id": 10,
        "domain_id": 1, "group_id": 2, "traffic_source_id": 3,
    }
    try:
        with TestClient(app, follow_redirects=False) as client:
            login = client.post(
                "/login", json={"username": AUTH_USERNAME, "password": AUTH_PASSWORD}, headers=SAME_ORIGIN
            )
            first = client.post(
                "/api/campaigns", json=payload,
                headers={**SAME_ORIGIN, "Idempotency-Key": "same-request"},
            )
            replay = client.post(
                "/api/campaigns", json=payload,
                headers={**SAME_ORIGIN, "Idempotency-Key": "same-request"},
            )
        assert login.status_code == 200
        assert first.status_code == 201
        assert replay.status_code == 201 and replay.json()["replayed"] is True
        assert sum(1 for method, path, _ in tracker.calls if method == "POST" and path == "campaigns") == 1
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_keitaro, None)
        app.dependency_overrides.pop(get_settings, None)
        engine.dispose()


def test_stream_response_keeps_api_schema_alias(authenticated_client) -> None:
    tracker = FakeKeitaro()
    app.dependency_overrides[get_keitaro] = lambda: tracker
    try:
        response = authenticated_client.get("/api/campaigns/21/streams")
        assert response.status_code == 200
        stream = response.json()[0]
        assert stream["schema"] == "landings"
        assert stream["editable"] is True
        assert sum(item["share"] for item in stream["offers"]) == 100
    finally:
        app.dependency_overrides.pop(get_keitaro, None)


def test_push_endpoint_updates_only_selected_stream_offers(authenticated_client) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    tracker = FakeKeitaro()

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_keitaro] = lambda: tracker
    try:
        streams = authenticated_client.get("/api/campaigns/21/streams")
        response = authenticated_client.post(
            "/api/campaigns/21/streams/77/offers/push",
            json={"offer_ids": [10, 20, 30], "expected_version": streams.json()[0]["version"]},
            headers=SAME_ORIGIN,
        )
        assert response.status_code == 200
        assert [offer["share"] for offer in response.json()["offers"]] == [33, 33, 34]
        assert tracker.stream["filters"] == [{"name": "country", "mode": "accept", "payload": ["US"]}]
        assert [path for method, path, _ in tracker.calls if method == "PUT"] == ["streams/77"]
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_keitaro, None)
        engine.dispose()
