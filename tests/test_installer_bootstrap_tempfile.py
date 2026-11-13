from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_installer_uses_unique_private_bootstrap_sql_file_and_cleanup_trap():
    script = (ROOT / "deploy/install.sh").read_text(encoding="utf-8")
    assert 'BOOTSTRAP_SQL="$(mktemp /tmp/vcar-role-bootstrap.XXXXXX.sql)"' in script
    assert 'trap \'rm -f "$BOOTSTRAP_SQL"\' EXIT' in script
    assert ' --write-role-sql "$BOOTSTRAP_SQL"' in script
    assert "/tmp/vcar-role-bootstrap.sql" not in script


def test_installer_verifies_database_backup_before_schema_or_alembic_migrations():
    script = (ROOT / "deploy/install.sh").read_text(encoding="utf-8")
    backup = 'bash "$PROJECT_DIR/scripts/backup_database.sh"'
    schema = 'psql -h 127.0.0.1 -U vcar -d vcar -v ON_ERROR_STOP=1 -f app/db/schema.sql'
    migration = '.venv/bin/alembic upgrade head'
    assert backup in script
    assert script.index(backup) < script.index(schema) < script.index(migration)
    assert 'set -Eeuo pipefail' in script



def test_installer_creates_target_database_before_applying_role_grants():
    script = (ROOT / "deploy/install.sh").read_text(encoding="utf-8")
    create_database = 'createdb -O vcar vcar'
    apply_role_grants = 'psql -d vcar -v ON_ERROR_STOP=1 -f \'$BOOTSTRAP_SQL\''
    assert create_database in script
    assert apply_role_grants in script
    assert script.index(create_database) < script.index(apply_role_grants)
