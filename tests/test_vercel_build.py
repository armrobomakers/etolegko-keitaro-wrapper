from __future__ import annotations

import subprocess

import pytest

from scripts import vercel_build


def test_preview_build_runs_tests_without_touching_production_database(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    environments: list[dict[str, str]] = []
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@db.example/app")
    monkeypatch.setattr(
        subprocess, "run", lambda args, **kwargs: (calls.append(list(args)), environments.append(kwargs["env"]))
    )

    vercel_build.main()

    assert len(calls) == 1
    assert calls[0][-2:] == ["pytest", "-q"]
    assert environments[0]["DATABASE_URL"] == "sqlite://"
    assert "POSTGRES_URL" not in environments[0]


def test_production_build_requires_postgres_before_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: calls.append(list(args)))

    with pytest.raises(RuntimeError, match="requires a PostgreSQL"):
        vercel_build.main()

    assert len(calls) == 1
    assert calls[0][-2:] == ["pytest", "-q"]


def test_production_build_runs_migrations_after_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_URL", "postgres://user:secret@db.example/app")
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: calls.append(list(args)))

    vercel_build.main()

    assert len(calls) == 2
    assert calls[0][-2:] == ["pytest", "-q"]
    assert calls[1][1:] == ["-m", "alembic", "upgrade", "head"]
