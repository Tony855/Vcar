import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, unquote


SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "bootstrap_env.py"


def _values(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def test_bootstrap_env_generates_split_roles_and_preserves_secrets(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=\nMIGRATION_DATABASE_URL=\nSECRET_KEY=REPLACE_WITH_AT_LEAST_32_RANDOM_BYTES\nADMIN_USERNAME=admin\n")
    role_sql = tmp_path / "roles.sql"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--env-file", str(env_file),
         "--print-db-password", "--print-migration-db-password",
         "--write-role-sql", str(role_sql)],
        check=True, capture_output=True, text=True,
    )
    runtime_password, migration_password = result.stdout.strip().splitlines()
    values = _values(env_file)
    runtime = urlsplit(values["DATABASE_URL"])
    migration = urlsplit(values["MIGRATION_DATABASE_URL"])

    assert runtime.username == "vcar_app"
    assert unquote(runtime.password or "") == runtime_password
    assert migration.username == "vcar"
    assert unquote(migration.password or "") == migration_password
    assert len(values["SECRET_KEY"]) >= 64
    sql = role_sql.read_text()
    assert "CREATE ROLE vcar_app LOGIN" in sql
    assert "ALTER ROLE vcar_app LOGIN PASSWORD" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES" in sql
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE vcar" in sql
    assert role_sql.stat().st_mode & 0o777 == 0o600
    assert env_file.stat().st_mode & 0o777 == 0o600

    first_runtime = values["DATABASE_URL"]
    first_migration = values["MIGRATION_DATABASE_URL"]
    first_secret = values["SECRET_KEY"]
    subprocess.run(
        [sys.executable, str(SCRIPT), "--env-file", str(env_file)],
        check=True, capture_output=True, text=True,
    )
    values_again = _values(env_file)
    assert values_again["DATABASE_URL"] == first_runtime
    assert values_again["MIGRATION_DATABASE_URL"] == first_migration
    assert values_again["SECRET_KEY"] == first_secret


def test_bootstrap_env_migrates_legacy_owner_url_to_migration_url(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql://vcar:legacy-password@127.0.0.1:5432/vcar\n"
        "SECRET_KEY=" + "x" * 64 + "\n"
    )
    subprocess.run([sys.executable, str(SCRIPT), "--env-file", str(env_file)], check=True)
    values = _values(env_file)
    runtime = urlsplit(values["DATABASE_URL"])
    migration = urlsplit(values["MIGRATION_DATABASE_URL"])
    assert runtime.username == "vcar_app"
    assert migration.username == "vcar"
    assert unquote(migration.password or "") == "legacy-password"


def test_bootstrap_env_refuses_remote_database(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql://vcar_app:secret@db.example.com:5432/vcar\n"
        "MIGRATION_DATABASE_URL=postgresql://vcar:owner@db.example.com:5432/vcar\n"
        "SECRET_KEY=" + "x" * 64 + "\n"
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--env-file", str(env_file)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "only provisions local PostgreSQL" in result.stderr
