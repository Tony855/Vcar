import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts" / "check_supported_os.sh"


def _check(tmp_path, *, distro, version, pretty_name):
    release = tmp_path / "os-release"
    release.write_text(
        f'ID="{distro}"\nVERSION_ID="{version}"\nPRETTY_NAME="{pretty_name}"\n',
        encoding="utf-8",
    )
    return subprocess.run(
        ["bash", str(CHECK), str(release)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_supported_ubuntu_2404_and_newer(tmp_path):
    result = _check(tmp_path, distro="ubuntu", version="24.04", pretty_name="Ubuntu 24.04 LTS")
    assert result.returncode == 0, result.stderr
    assert "Ubuntu 24.04 LTS" in result.stdout


def test_rejects_ubuntu_2204(tmp_path):
    result = _check(tmp_path, distro="ubuntu", version="22.04", pretty_name="Ubuntu 22.04 LTS")
    assert result.returncode == 1
    assert "Ubuntu 24.04 or newer is required" in result.stderr


def test_supported_debian_13_and_newer(tmp_path):
    result = _check(tmp_path, distro="debian", version="13", pretty_name="Debian GNU/Linux 13")
    assert result.returncode == 0, result.stderr


def test_rejects_debian_12_and_unknown_distributions(tmp_path):
    debian = _check(tmp_path, distro="debian", version="12", pretty_name="Debian 12")
    unsupported = _check(tmp_path, distro="fedora", version="42", pretty_name="Fedora 42")
    assert debian.returncode == 1
    assert "Debian 13 or newer is required" in debian.stderr
    assert unsupported.returncode == 1
    assert "Unsupported Linux distribution" in unsupported.stderr
