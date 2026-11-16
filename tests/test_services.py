from __future__ import annotations

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.keitaro import KeitaroClient, KeitaroError
from app.models import Operation
from app.schemas import CampaignCreateInput
from app.services import create_campaign, distribute_equally, offer_signature, update_stream_offers


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def settings() -> Settings:
    return Settings("https://tracker.example", "not-a-real-key", "sqlite:///:memory:", "test_ryzhkov_", "")


class FakeKeitaro:
    def __init__(self, *, fail_second_stream: bool = False) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.fail_second_stream = fail_second_stream
        self.fail_enable = False
        self.fail_disable = False
        self.stream_posts = 0
        self.stream = {
            "id": 77, "campaign_id": 21, "name": "Default offers", "schema": "landings", "type": "default",
            "offers": [{"offer_id": 10, "share": 50, "state": "active"},
                       {"offer_id": 20, "share": 50, "state": "active"}],
            "filters": [{"name": "country", "mode": "accept", "payload": ["US"]}],
            "action_options": {"keep": "this"},
        }

    def get(self, path: str, *, params=None):
        self.calls.append(("GET", path, params))
        catalogs = {
            "domains": [{"id": 1, "name": "tracker.example", "state": "active"}],
            "groups": [{"id": 2, "name": "Tests", "state": "active"}],
            "traffic_sources": [{"id": 3, "name": "Source", "state": "active"}],
            "offers": [{"id": 10, "name": "Offer A", "state": "active"},
                       {"id": 20, "name": "Offer B", "state": "active"},
                       {"id": 30, "name": "Offer C", "state": "active"}],
            "stream_filters": [{"value": "country", "tooltip": "Country"}],
            "streams_actions": [{"key": "http", "name": "HTTP redirect", "field": "url", "type": "string"}],
        }
        if path in catalogs:
            return catalogs[path]
        if path == "streams/77":
            return self.stream
        if path == "campaigns/21/streams":
            return [self.stream]
        raise AssertionError(f"Unexpected GET {path}")

    def post(self, path: str, body: dict | None = None):
        self.calls.append(("POST", path, body))
        if path == "campaigns":
            return {"id": 21, "alias": body["alias"], "state": body["state"]}
        if path == "streams":
            self.stream_posts += 1
            if self.fail_second_stream and self.stream_posts == 2:
                raise KeitaroError("Keitaro API POST streams вернул HTTP 500", status_code=500)
            return {"id": 100 + self.stream_posts}
        if path == "campaigns/21/enable":
            if self.fail_enable:
                raise KeitaroError("Keitaro API POST campaigns/21/enable вернул HTTP 500", status_code=500)
            return {"id": 21, "state": "active"}
        if path == "campaigns/21/disable":
            if self.fail_disable:
                raise KeitaroError("Keitaro API POST campaigns/21/disable вернул HTTP 500", status_code=500)
            return {"id": 21, "state": "disabled"}
        raise AssertionError(f"Unexpected POST {path}")

    def put(self, path: str, body: dict):
        self.calls.append(("PUT", path, body))
        assert set(body) == {"offers"}
        self.stream["offers"] = body["offers"]
        return self.stream


def campaign_input() -> CampaignCreateInput:
    return CampaignCreateInput(name="Весенний тест", country_codes=["us", "ca"], offer_id=10,
                               domain_id=1, group_id=2, traffic_source_id=3)


def test_create_campaign_orders_and_enables_only_after_two_streams(db: Session, settings: Settings) -> None:
    client = FakeKeitaro()
    result = create_campaign(client, db, settings, campaign_input(), "request-123")

    assert result["name"] == "test_ryzhkov_vesenniy_test"
    assert result["stream_ids"] == [101, 102]
    assert result["state"] == "active"
    campaign_body = next(body for method, path, body in client.calls if method == "POST" and path == "campaigns")
    assert campaign_body["state"] == "disabled"
    assert campaign_body["group_id"] == "2"
    posts = [(path, body) for method, path, body in client.calls if method == "POST"]
    assert [path for path, _ in posts] == ["campaigns", "streams", "streams", "campaigns/21/enable"]
    geo_body = posts[1][1]
    assert geo_body["filters"] == [{"name": "country", "mode": "accept", "payload": ["US", "CA"]}]
    assert geo_body["action_options"] == {"url": "https://www.google.com/"}
    assert posts[2][1]["type"] == "default"
    assert posts[2][1]["offers"] == [{"offer_id": 10, "share": 100, "state": "active"}]
    operation = db.scalar(select(Operation).where(Operation.idempotency_key == "request-123"))
    assert operation is not None and operation.status == "succeeded"


def test_partial_creation_failure_never_enables_campaign(db: Session, settings: Settings) -> None:
    client = FakeKeitaro(fail_second_stream=True)
    with pytest.raises(KeitaroError):
        create_campaign(client, db, settings, campaign_input(), "request-fail")

    assert not any(path.endswith("/enable") for method, path, _ in client.calls)
    operation = db.scalar(select(Operation).where(Operation.idempotency_key == "request-fail"))
    assert operation is not None and operation.status == "failed"
    assert '"campaign_state_after_failure": "disabled"' in operation.summary_json


def test_enable_error_triggers_disable_recovery(db: Session, settings: Settings) -> None:
    client = FakeKeitaro()
    client.fail_enable = True
    with pytest.raises(KeitaroError):
        create_campaign(client, db, settings, campaign_input(), "request-enable-fail")

    paths = [path for method, path, _ in client.calls if method == "POST"]
    assert paths[-2:] == ["campaigns/21/enable", "campaigns/21/disable"]
    operation = db.scalar(select(Operation).where(Operation.idempotency_key == "request-enable-fail"))
    assert operation is not None and operation.status == "failed"
    assert '"campaign_state_after_failure": "disabled"' in operation.summary_json


def test_failed_disable_recovery_reports_unknown_remote_state(db: Session, settings: Settings) -> None:
    client = FakeKeitaro()
    client.fail_enable = True
    client.fail_disable = True
    with pytest.raises(KeitaroError):
        create_campaign(client, db, settings, campaign_input(), "request-unknown-state")
    operation = db.scalar(select(Operation).where(Operation.idempotency_key == "request-unknown-state"))
    assert operation is not None and operation.status == "failed"
    assert '"campaign_state_after_failure": "unknown"' in operation.summary_json


def test_even_distribution_is_integer_and_sums_to_100() -> None:
    shares = distribute_equally([3, 4, 5])
    assert shares == [
        {"offer_id": 3, "share": 33, "state": "active"},
        {"offer_id": 4, "share": 33, "state": "active"},
        {"offer_id": 5, "share": 34, "state": "active"},
    ]
    assert sum(item["share"] for item in shares) == 100


def test_geo_codes_accept_multiple_values_and_deduplicate() -> None:
    request = CampaignCreateInput(name="test", country_codes="mx, AU, MX", offer_id=10,
                                  domain_id=1, group_id=2, traffic_source_id=3)
    assert request.country_codes == ["MX", "AU"]
    with pytest.raises(ValueError, match="GEO-код"):
        CampaignCreateInput(name="test", country_codes=["M!"], offer_id=10,
                            domain_id=1, group_id=2, traffic_source_id=3)


def test_offer_push_preserves_other_stream_fields_and_checks_result(db: Session) -> None:
    client = FakeKeitaro()
    old_version = offer_signature(client.stream["offers"])
    result = update_stream_offers(client, db, campaign_id=21, stream_id=77, offer_ids=[10, 20, 30],
                                  expected_version=old_version)

    assert sum(item["share"] for item in result["offers"]) == 100
    assert [item["share"] for item in result["offers"]] == [33, 33, 34]
    assert client.stream["filters"] == [{"name": "country", "mode": "accept", "payload": ["US"]}]
    assert client.stream["action_options"] == {"keep": "this"}
    put = next(body for method, path, body in client.calls if method == "PUT")
    assert list(put) == ["offers"]


def test_offer_push_rejects_stale_version_and_empty_stream(db: Session) -> None:
    client = FakeKeitaro()
    with pytest.raises(RuntimeError, match="изменился"):
        update_stream_offers(client, db, campaign_id=21, stream_id=77, offer_ids=[10, 20, 30],
                             expected_version="0" * 64)

    with pytest.raises(ValueError, match="хотя бы один"):
        update_stream_offers(client, db, campaign_id=21, stream_id=77, offer_ids=[],
                             expected_version=offer_signature(client.stream["offers"]))


def test_api_client_does_not_expose_response_body_or_key_in_error() -> None:
    secret = "not-a-real-key"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Api-Key"] == secret
        return httpx.Response(401, text=f"server echoed {secret}")

    client = KeitaroClient(Settings("https://tracker.example", secret, "sqlite:///:memory:", "test_ryzhkov_", ""),
                           transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(KeitaroError) as exc_info:
            client.get("offers")
        assert "401" in str(exc_info.value)
        assert secret not in str(exc_info.value)
    finally:
        client.close()
