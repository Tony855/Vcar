import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "publish_frontend.sh"


def run_publish(source, target, *, path=None):
    return subprocess.run(
        ["bash", str(SCRIPT), str(source), str(target)],
        env=os.environ | ({"PATH": path} if path else {}),
        text=True,
        capture_output=True,
        check=False,
    )


def test_publish_frontend_replaces_assets_only_after_complete_build(tmp_path):
    source = tmp_path / "dist"
    source.mkdir()
    (source / "index.html").write_text("new build", encoding="utf-8")
    target = tmp_path / "app" / "web" / "static"
    target.mkdir(parents=True)
    (target / "index.html").write_text("old build", encoding="utf-8")

    result = run_publish(source, target)

    assert result.returncode == 0, result.stderr
    assert (target / "index.html").read_text(encoding="utf-8") == "new build"
    assert not list(target.parent.glob(".static.previous.*"))
    assert not list(target.parent.glob(".static.new.*"))


def test_publish_frontend_rejects_incomplete_build_without_touching_current(tmp_path):
    source = tmp_path / "dist"
    source.mkdir()
    target = tmp_path / "app" / "web" / "static"
    target.mkdir(parents=True)
    (target / "index.html").write_text("working build", encoding="utf-8")

    result = run_publish(source, target)

    assert result.returncode == 1
    assert (target / "index.html").read_text(encoding="utf-8") == "working build"


def test_publish_frontend_restores_previous_assets_when_swap_fails(tmp_path):
    source = tmp_path / "dist"
    source.mkdir()
    (source / "index.html").write_text("new build", encoding="utf-8")
    target = tmp_path / "app" / "web" / "static"
    target.mkdir(parents=True)
    (target / "index.html").write_text("working build", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real_mv = subprocess.run(["bash", "-lc", "command -v mv"], capture_output=True, text=True).stdout.strip()
    shim = bin_dir / "mv"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f"if [[ \"$1\" == *'.static.new.'* && \"$2\" == \"{target}\" ]]; then exit 9; fi\n"
        f"exec \"{real_mv}\" \"$@\"\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)

    result = run_publish(source, target, path=f"{bin_dir}:{os.environ['PATH']}")

    assert result.returncode == 1
    assert (target / "index.html").read_text(encoding="utf-8") == "working build"
