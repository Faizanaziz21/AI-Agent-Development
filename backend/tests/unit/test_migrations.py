"""The Alembic history must build the exact schema the ORM models describe."""

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def _alembic(db: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "AGENTOS_DATABASE_URL": f"sqlite+aiosqlite:///{db}"}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)


def test_migrations_match_models_and_round_trip(tmp_path):
    db = tmp_path / "migrations.db"
    up = _alembic(db, "upgrade", "head")
    assert up.returncode == 0, up.stderr
    check = _alembic(db, "check")
    assert check.returncode == 0, f"models have drifted from migrations; run `alembic revision --autogenerate`\n{check.stderr}"
    down = _alembic(db, "downgrade", "base")
    assert down.returncode == 0, down.stderr
