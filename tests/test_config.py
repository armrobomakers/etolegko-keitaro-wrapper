from __future__ import annotations

from app.config import get_settings


def test_campaign_prefix_removes_literal_and_actual_newlines(monkeypatch) -> None:
    monkeypatch.setenv("CAMPAIGN_PREFIX", "test_ryzhkov_\\n\r\n")

    assert get_settings().campaign_prefix == "test_ryzhkov_"
