import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, unquote


SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "bootstrap_env.py"


def test_bootstrap_env_generates_and_preserves_secrets(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=\nSECRET_KEY=REPLACE_WITH_AT_LEAST_32_RANDOM_BYTES\nADMIN_USERNAME=admin\n")
    role_sql = tmp_path / "role.sql"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--env-file", str(env_file),
         "--print-db-password", "--write-role-sql", str(role_sql)],
        check=True, capture_output=True, text=True,
    )
    password = result.stdout.strip()
    values = dict(line.split("=", 1) for line in env_file.read_text().splitlines() if "=" in line)
    parsed = urlsplit(values["DATABASE_URL"])

    assert len(password) == 64
    assert unquote(parsed.password or "") == password
    assert len(values["SECRET_KEY"]) >= 64
    assert f"PASSWORD '{password}'" in role_sql.read_text()
    assert role_sql.stat().st_mode & 0o777 == 0o600
    assert env_file.stat().st_mode & 0o777 == 0o600

    first_url = values["DATABASE_URL"]
    first_secret = values["SECRET_KEY"]
    subprocess.run(
        [sys.executable, str(SCRIPT), "--env-file", str(env_file)],
        check=True, capture_output=True, text=True,
    )
    values_again = dict(line.split("=", 1) for line in env_file.read_text().splitlines() if "=" in line)
    assert values_again["DATABASE_URL"] == first_url
    assert values_again["SECRET_KEY"] == first_secret


def test_bootstrap_env_refuses_remote_database(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgresql://vcar:secret@db.example.com:5432/vcar\nSECRET_KEY=" + "x" * 64 + "\n")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--env-file", str(env_file)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "only provisions a local PostgreSQL" in result.stderr
