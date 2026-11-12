import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup_database.sh"


def _run_backup(tmp_path, env_text, *, fail_list=False, preexisting_backup_dir_mode=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    dump_log = tmp_path / "dump-args"
    (bin_dir / "pg_dump").write_text(
        "#!/usr/bin/env bash\nprintf '%s\n' \"$@\" > \"$DUMP_LOG\"\n"
        "while (($#)); do if [[ \"$1\" == --file=* ]]; then "
        "printf 'test dump' > \"${1#--file=}\"; fi; shift; done\n",
        encoding="utf-8",
    )
    (bin_dir / "pg_restore").write_text(
        "#!/usr/bin/env bash\n[[ \"$1\" == --list && -s \"$2\" ]]\n",
        encoding="utf-8",
    )
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    env_file = tmp_path / ".env"
    env_file.write_text(env_text, encoding="utf-8")
    backup_dir = tmp_path / "backups"
    if preexisting_backup_dir_mode is not None:
        backup_dir.mkdir()
        backup_dir.chmod(preexisting_backup_dir_mode)
    if fail_list:
        (bin_dir / "pg_restore").write_text(
            "#!/usr/bin/env bash\nexit 7\n", encoding="utf-8"
        )
        (bin_dir / "pg_restore").chmod(0o755)
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DUMP_LOG": str(dump_log),
        "VCAR_ENV_FILE": str(env_file),
        "VCAR_BACKUP_DIR": str(backup_dir),
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, text=True, capture_output=True, check=False
    )
    return result, backup_dir, dump_log


def test_backup_prefers_migration_database_url_and_secures_verified_dump(tmp_path):
    result, backup_dir, dump_log = _run_backup(
        tmp_path,
        "DATABASE_URL=postgresql://runtime:secret@localhost/vcar\n"
        "MIGRATION_DATABASE_URL=postgresql://owner:secret@localhost/vcar\n",
    )
    assert result.returncode == 0, result.stderr
    args = dump_log.read_text(encoding="utf-8")
    assert "owner:secret" in args
    assert "runtime:secret" not in args
    dumps = list(backup_dir.glob("vcar-*.dump"))
    assert len(dumps) == 1
    assert dumps[0].read_bytes() == b"test dump"
    assert dumps[0].stat().st_mode & 0o777 == 0o600
    assert not list(backup_dir.glob("*.tmp"))


def test_backup_uses_legacy_database_url_only_when_migration_url_missing(tmp_path):
    result, _, dump_log = _run_backup(
        tmp_path, "DATABASE_URL=postgresql://legacy:secret@localhost/vcar\n"
    )
    assert result.returncode == 0, result.stderr
    assert "legacy:secret" in dump_log.read_text(encoding="utf-8")


def test_backup_fails_closed_when_no_database_url_is_configured(tmp_path):
    result, _, _ = _run_backup(tmp_path, "OTHER=value\n")
    assert result.returncode == 1
    assert "missing" in result.stderr


def test_concurrent_backups_started_in_same_second_never_overwrite_each_other(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "date").write_text(
        """#!/usr/bin/env bash
echo 20261112T120000Z
""",
        encoding="utf-8",
    )
    (bin_dir / "pg_dump").write_text(
        """#!/usr/bin/env bash
sleep 0.2
while (($#)); do
  if [[ "$1" == --file=* ]]; then
    output="$(printf '%s' "$1" | sed 's/^--file=//')"
    printf 'unique dump %s' "$$" > "$output"
  fi
  shift
done
""",
        encoding="utf-8",
    )
    (bin_dir / "pg_restore").write_text(
        """#!/usr/bin/env bash
[[ "$1" == --list && -s "$2" ]]
""",
        encoding="utf-8",
    )
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MIGRATION_DATABASE_URL=postgresql://owner:secret@localhost/vcar\n",
        encoding="utf-8",
    )
    backup_dir = tmp_path / "backups"
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "VCAR_ENV_FILE": str(env_file),
        "VCAR_BACKUP_DIR": str(backup_dir),
    }

    processes = [
        subprocess.Popen(
            ["bash", str(SCRIPT)],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(2)
    ]
    results = [process.communicate(timeout=10) for process in processes]

    assert [process.returncode for process in processes] == [0, 0], results
    dumps = list(backup_dir.glob("vcar-20261112T120000Z-*.dump"))
    assert len(dumps) == 2
    assert len({dump.read_bytes() for dump in dumps}) == 2
    assert not list(backup_dir.glob("*.tmp"))



def test_backup_restricts_permissions_on_preexisting_backup_directory(tmp_path):
    result, backup_dir, _ = _run_backup(
        tmp_path,
        "MIGRATION_DATABASE_URL=postgresql://owner:secret@localhost/vcar\n",
        preexisting_backup_dir_mode=0o755,
    )
    assert result.returncode == 0, result.stderr
    assert backup_dir.stat().st_mode & 0o777 == 0o700


def test_backup_does_not_publish_archive_when_integrity_listing_fails(tmp_path):
    result, backup_dir, _ = _run_backup(
        tmp_path,
        "MIGRATION_DATABASE_URL=postgresql://owner:secret@localhost/vcar\n",
        fail_list=True,
    )
    assert result.returncode == 7
    assert not list(backup_dir.glob("vcar-*.dump"))
    assert not list(backup_dir.glob("*.tmp"))
