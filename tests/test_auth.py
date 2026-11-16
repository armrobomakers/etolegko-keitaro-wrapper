from app.auth import create_session_token, is_valid_session, origin_matches_host
from app.config import Settings, get_settings


def test_signed_session_expires_and_rejects_tampering() -> None:
    settings = Settings(
        "", "", "", "", "", app_username="admin", app_password="a-long-test-password",
        app_session_secret="a-session-signing-secret-that-is-long-enough",
        app_session_ttl_seconds=300,
    )
    token = create_session_token(settings, now=1_000)
    assert is_valid_session(settings, token, now=1_100)
    assert not is_valid_session(settings, token, now=1_301)
    assert not is_valid_session(settings, token + "x", now=1_100)


def test_origin_matching_checks_host_and_port_including_ipv6() -> None:
    assert origin_matches_host("http://localhost:8000", "localhost:8000")
    assert origin_matches_host("http://[::1]:8000", "[::1]:8000")
    assert not origin_matches_host("http://localhost:9000", "localhost:8000")
    assert not origin_matches_host("https://attacker.example", "app.example")


def test_database_url_accepts_vercel_postgres_variable_and_psycopg_urls(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_URL", "postgres://user:secret@db.example/app")
    settings = get_settings()
    assert settings.database_url == "postgresql+psycopg://user:secret@db.example/app"
    assert settings.database_is_postgres

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@db.example/primary")
    settings = get_settings()
    assert settings.database_url == "postgresql+psycopg://user:secret@db.example/primary"
