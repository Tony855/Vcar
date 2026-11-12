import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_staging_load_profiles.sh"


def _fake_python(tmp_path):
    executable = tmp_path / "fake-python"
    executable.write_text(
        """#!/usr/bin/env python3
import os
import pathlib
import sys
args = sys.argv[1:]
connections = args[args.index("--connections") + 1]
output = pathlib.Path(args[args.index("--json-output") + 1])
output.parent.mkdir(parents=True, exist_ok=True)
if os.getenv("FAKE_FAIL_CONNECTIONS") == connections:
    raise SystemExit(9)
output.write_text('{"failures": 0}\\n', encoding="utf-8")
print("fake profile", connections)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _run(tmp_path, *, ack="I_CONFIRM_ISOLATED_STAGING", fail=None):
    output = tmp_path / "reports"
    env = os.environ | {
        "PYTHON_BIN": str(_fake_python(tmp_path)),
        "VCAR_BASE_URL": "https://staging.vcar.invalid",
        "VCAR_STAGING_ACK": ack,
        "VCAR_LOAD_OUTPUT_DIR": str(output),
        "VCAR_LOAD_COOLDOWN_SECONDS": "0",
        "DRIVER_TOKEN": "do-not-print-driver-token",
        "ADMIN_TOKEN": "do-not-print-admin-token",
    }
    if fail:
        env["FAKE_FAIL_CONNECTIONS"] = fail
    return subprocess.run(
        ["bash", str(SCRIPT)], env=env, text=True, capture_output=True, check=False
    ), output


def test_staging_profiles_run_50_100_250_500_and_save_private_reports(tmp_path):
    result, output = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert {path.name for path in output.glob("gps-*.json")} == {
        "gps-50.json", "gps-100.json", "gps-250.json", "gps-500.json"
    }
    assert output.stat().st_mode & 0o777 == 0o700
    assert "All four staging profiles passed" in result.stdout
    assert "do-not-print" not in result.stdout + result.stderr


def test_staging_profiles_stop_immediately_after_first_failed_tier(tmp_path):
    result, output = _run(tmp_path, fail="100")
    assert result.returncode == 9
    assert (output / "gps-50.json").exists()
    assert not (output / "gps-250.json").exists()
    assert not (output / "gps-500.json").exists()


def test_staging_profiles_refuse_without_explicit_isolated_staging_ack(tmp_path):
    result, output = _run(tmp_path, ack="")
    assert result.returncode == 2
    assert "Refusing load test" in result.stderr
    assert not output.exists()


def test_staging_profiles_require_short_lived_tokens(tmp_path):
    env = os.environ | {
        "PYTHON_BIN": str(_fake_python(tmp_path)),
        "VCAR_BASE_URL": "https://staging.vcar.invalid",
        "VCAR_STAGING_ACK": "I_CONFIRM_ISOLATED_STAGING",
        "VCAR_LOAD_OUTPUT_DIR": str(tmp_path / "reports"),
        "VCAR_LOAD_COOLDOWN_SECONDS": "0",
        "DRIVER_TOKEN": "",
        "ADMIN_TOKEN": "admin",
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, text=True, capture_output=True, check=False
    )
    assert result.returncode == 2
    assert "DRIVER_TOKEN and ADMIN_TOKEN" in result.stderr
