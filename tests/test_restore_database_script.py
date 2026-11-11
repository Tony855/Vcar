import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "restore_database.sh"


def _run_restore(tmp_path, *, env_text, confirm="YES", fail_restore=False, fail_dump=False):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    command_log = tmp_path / "commands.log"
    (bin_dir / "systemctl").write_text(
        "#!/usr/bin/env bash\nprintf 'systemctl %s\\n' \"$*\" >> \"$COMMAND_LOG\"\n",
        encoding="utf-8",
    )
    (bin_dir / "pg_dump").write_text(
        "#!/usr/bin/env bash\nprintf 'pg_dump %s\\n' \"$*\" >> \"$COMMAND_LOG\"\n"
        "if [[ \"$FAIL_DUMP\" == 1 ]]; then exit 8; fi\n"
        "while (($#)); do if [[ \"$1\" == --file=* ]]; then "
        "printf 'pre-restore dump' > \"${1#--file=}\"; fi; shift; done\n",
        encoding="utf-8",
    )
    (bin_dir / "pg_restore").write_text(
        "#!/usr/bin/env bash\nprintf 'pg_restore %s\\n' \"$*\" >> \"$COMMAND_LOG\"\n"
        "if [[ \"$1\" == --list ]]; then exit 0; fi\n"
        "if [[ \"$FAIL_RESTORE\" == 1 ]]; then exit 9; fi\n",
        encoding="utf-8",
    )
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    env_file = tmp_path / ".env"
    env_file.write_text(env_text, encoding="utf-8")
    dump = tmp_path / "backup.dump"
    dump.write_bytes(b"verified-test-dump")
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "COMMAND_LOG": str(command_log),
        "VCAR_ENV_FILE": str(env_file),
        "VCAR_BACKUP_DIR": str(tmp_path / "pre-restore-backups"),
        "CONFIRM_RESTORE": confirm,
        "FAIL_RESTORE": "1" if fail_restore else "0",
        "FAIL_DUMP": "1" if fail_dump else "0",
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), str(dump)],
        env=env, text=True, capture_output=True, check=False,
    )
    log = command_log.read_text(encoding="utf-8") if command_log.exists() else ""
    return result, log


def test_restore_requires_explicit_destructive_confirmation(tmp_path):
    result, log = _run_restore(
        tmp_path,
        env_text="MIGRATION_DATABASE_URL=postgresql://owner:secret@localhost/vcar\n",
        confirm="NO",
    )
    assert result.returncode == 2
    assert "Refusing destructive restore" in result.stderr
    assert log == ""


def test_restore_uses_schema_owner_and_restarts_services_after_success(tmp_path):
    result, log = _run_restore(
        tmp_path,
        env_text=(
            "DATABASE_URL=postgresql://runtime:secret@localhost/vcar\n"
            "MIGRATION_DATABASE_URL=postgresql://owner:secret@localhost/vcar\n"
        ),
    )
    assert result.returncode == 0, result.stderr
    assert "pg_restore --clean" in log
    assert "owner:secret" in log
    assert "runtime:secret" not in log
    assert "pg_dump" in log
    assert log.index("pg_dump") < log.index("systemctl stop vcar-bot vcar-web")
    assert "systemctl stop vcar-bot vcar-web" in log
    assert "systemctl start vcar-web vcar-bot" in log


def test_restore_restarts_services_when_restore_command_fails(tmp_path):
    result, log = _run_restore(
        tmp_path,
        env_text="MIGRATION_DATABASE_URL=postgresql://owner:secret@localhost/vcar\n",
        fail_restore=True,
    )
    assert result.returncode == 9
    assert "systemctl stop vcar-bot vcar-web" in log
    assert "systemctl start vcar-web vcar-bot" in log


def test_restore_fails_closed_without_schema_owner_url(tmp_path):
    result, log = _run_restore(tmp_path, env_text="OTHER=value\n")
    assert result.returncode == 1
    assert "missing" in result.stderr
    assert log == ""



def test_restore_aborts_before_stopping_services_when_pre_restore_backup_fails(tmp_path):
    result, log = _run_restore(
        tmp_path,
        env_text="MIGRATION_DATABASE_URL=postgresql://owner:secret@localhost/vcar\n",
        fail_dump=True,
    )
    assert result.returncode != 0
    assert "pg_dump" in log
    assert "systemctl stop vcar-bot vcar-web" not in log
    assert "pg_restore --clean" not in log
