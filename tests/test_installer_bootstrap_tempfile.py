from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_installer_uses_unique_private_bootstrap_sql_file_and_cleanup_trap():
    script = (ROOT / "deploy/install.sh").read_text(encoding="utf-8")
    assert 'BOOTSTRAP_SQL="$(mktemp /tmp/vcar-role-bootstrap.XXXXXX.sql)"' in script
    assert 'trap \'rm -f "$BOOTSTRAP_SQL"\' EXIT' in script
    assert ' --write-role-sql "$BOOTSTRAP_SQL"' in script
    assert "/tmp/vcar-role-bootstrap.sql" not in script
