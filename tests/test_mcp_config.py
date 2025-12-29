def test_ensure_global_mcp_config_creates_file(tmp_path, monkeypatch):
    from chad.mcp_config import ensure_global_mcp_config, _config_path

    # isolate HOME so we don't touch the real config
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_path = _config_path()
    result = ensure_global_mcp_config(project_root=tmp_path)

    assert result["changed"] is True
    assert cfg_path.exists()
    text = cfg_path.read_text()
    assert "[mcp_servers.chad-ui-playwright]" in text
    assert f'cwd = "{tmp_path}"' in text
    assert f'PYTHONPATH = "{tmp_path / "src"}"' in text


def test_ensure_global_mcp_config_idempotent(tmp_path, monkeypatch):
    from chad.mcp_config import ensure_global_mcp_config

    monkeypatch.setenv("HOME", str(tmp_path))
    first = ensure_global_mcp_config(project_root=tmp_path)
    second = ensure_global_mcp_config(project_root=tmp_path)

    assert first["changed"] is True
    assert second["changed"] is False
