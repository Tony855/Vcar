from pathlib import Path

def test_standalone_osrm_installer_runs_prepare_and_smoke_checks():
    script = (Path(__file__).resolve().parents[1] / "deploy" / "install-osrm.sh").read_text()
    assert "docker compose -f docker-compose.osrm.yml --profile prepare run --rm osrm-prepare" in script
    assert "scripts/smoke_osrm.py" in script
    assert "Main Vcar installation does not require OSRM" in script
    assert "Installing the missing Docker Compose plugin" in script
