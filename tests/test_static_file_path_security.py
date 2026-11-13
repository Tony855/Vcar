import pytest

from app.web import server


@pytest.mark.asyncio
async def test_serve_spa_does_not_expose_files_outside_static_root(tmp_path, monkeypatch):
    static_root = tmp_path / "static"
    static_root.mkdir()
    (static_root / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("private deployment secret", encoding="utf-8")
    monkeypatch.setattr(server, "static_dir", str(static_root))

    response = await server.serve_spa("../secret.txt")

    assert response.path == str(static_root / "index.html")
    assert response.path != str(tmp_path / "secret.txt")


@pytest.mark.asyncio
async def test_serve_spa_does_not_follow_symlink_outside_static_root(tmp_path, monkeypatch):
    static_root = tmp_path / "static"
    static_root.mkdir()
    (static_root / "index.html").write_text("<html>app</html>", encoding="utf-8")
    outside = tmp_path / "private.txt"
    outside.write_text("private deployment secret", encoding="utf-8")
    (static_root / "private-link.txt").symlink_to(outside)
    monkeypatch.setattr(server, "static_dir", str(static_root))

    response = await server.serve_spa("private-link.txt")

    assert response.path == str(static_root / "index.html")
    assert response.path != str(outside)
