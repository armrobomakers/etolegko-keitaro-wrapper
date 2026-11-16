from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings
from app.db import Base
from app.main import app, get_db, get_keitaro
from tests.test_services import FakeKeitaro


def test_home_page_and_static_assets_are_served() -> None:
    with TestClient(app) as client:
        page = client.get("/")
        script = client.get("/static/app.js")
        css = client.get("/static/styles.css")
        editor_css = client.get("/static/editor.css")
    assert page.status_code == 200
    assert "Новая кампания" in page.text
    assert script.status_code == 200 and "Idempotency-Key" in script.text
    assert css.status_code == 200 and ".offer-table" in css.text
    assert editor_css.status_code == 200 and ".stream-card" in editor_css.text


def test_health_never_returns_keitaro_secret() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert "api_key" not in response.text.lower()
    assert "password" not in response.text.lower()


def test_campaign_endpoint_is_idempotent_with_same_key() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    tracker = FakeKeitaro()
    settings = Settings("https://tracker.example", "not-a-real-key", "sqlite:///:memory:", "test_ryzhkov_", "")

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_keitaro] = lambda: tracker
    app.dependency_overrides[get_settings] = lambda: settings
    payload = {"name": "API test", "country_codes": ["US"], "offer_id": 10,
               "domain_id": 1, "group_id": 2, "traffic_source_id": 3}
    try:
        with TestClient(app) as client:
            first = client.post("/api/campaigns", json=payload, headers={"Idempotency-Key": "same-request"})
            replay = client.post("/api/campaigns", json=payload, headers={"Idempotency-Key": "same-request"})
        assert first.status_code == 201
        assert replay.status_code == 201 and replay.json()["replayed"] is True
        assert sum(1 for method, path, _ in tracker.calls if method == "POST" and path == "campaigns") == 1
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_keitaro, None)
        app.dependency_overrides.pop(get_settings, None)
        engine.dispose()


def test_stream_response_keeps_api_schema_alias() -> None:
    tracker = FakeKeitaro()
    app.dependency_overrides[get_keitaro] = lambda: tracker
    try:
        with TestClient(app) as client:
            response = client.get("/api/campaigns/21/streams")
        assert response.status_code == 200
        stream = response.json()[0]
        assert stream["schema"] == "landings"
        assert stream["editable"] is True
        assert sum(item["share"] for item in stream["offers"]) == 100
    finally:
        app.dependency_overrides.pop(get_keitaro, None)


def test_push_endpoint_updates_only_selected_stream_offers() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    tracker = FakeKeitaro()

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_keitaro] = lambda: tracker
    try:
        with TestClient(app) as client:
            streams = client.get("/api/campaigns/21/streams")
            response = client.post("/api/campaigns/21/streams/77/offers/push", json={
                "offer_ids": [10, 20, 30], "expected_version": streams.json()[0]["version"],
            })
        assert response.status_code == 200
        assert [offer["share"] for offer in response.json()["offers"]] == [33, 33, 34]
        assert tracker.stream["filters"] == [{"name": "country", "mode": "accept", "payload": ["US"]}]
        assert [path for method, path, _ in tracker.calls if method == "PUT"] == ["streams/77"]
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_keitaro, None)
        engine.dispose()
