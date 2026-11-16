from __future__ import annotations

import os
import subprocess
import sys


def _test_environment() -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "POSTGRES_URL", "KEITARO_BASE_URL", "KEITARO_API_KEY", "APP_USERNAME",
        "APP_PASSWORD", "APP_SESSION_SECRET", "APP_COOKIE_SECURE", "APP_SESSION_TTL_SECONDS",
    ):
        env.pop(key, None)
    env["DATABASE_URL"] = "sqlite://"
    env["VERCEL"] = "0"
    return env


def main() -> None:
    subprocess.run([sys.executable, "-m", "pytest", "-q"], check=True, env=_test_environment())

    if os.getenv("VERCEL_ENV", "").strip().lower() != "production":
        print("Preview build: tests passed; production database migrations skipped.")
        return

    database_url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or ""
    if not database_url.lower().startswith(("postgres://", "postgresql://", "postgresql+")):
        raise RuntimeError("Production deployment requires a PostgreSQL DATABASE_URL or POSTGRES_URL.")

    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)


if __name__ == "__main__":
    main()
