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
    assert f'cwd = "{tmp_path / "src"}"' in text
    assert f'PYTHONPATH = "{tmp_path / "src"}"' in text


def test_ensure_global_mcp_config_idempotent(tmp_path, monkeypatch):
    from chad.mcp_config import ensure_global_mcp_config

    monkeypatch.setenv("HOME", str(tmp_path))
    first = ensure_global_mcp_config(project_root=tmp_path)
    second = ensure_global_mcp_config(project_root=tmp_path)

    assert first["changed"] is True
    assert second["changed"] is False


def test_ensure_global_mcp_config_cleans_dangling_entries(tmp_path, monkeypatch):
    import tomllib

    from chad.mcp_config import ensure_global_mcp_config, _config_path

    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_path = _config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        '[mcp_servers.chad-ui-playwright]\n'
        'command = "python3"\n'
        'args = ["-m", "chad.mcp_playwright"]\n'
        'cwd = "/tmp/bad"\n'
        'env = { PYTHONPATH = "/tmp/bad/src" }\n'
        '["-m", "chad.mcp_playwright"]\n'
        'cwd = "/tmp/bad2"\n'
        'env = { PYTHONPATH = "/tmp/bad2/src" }\n'
    )

    result = ensure_global_mcp_config(project_root=tmp_path)
    parsed = tomllib.loads(cfg_path.read_text())

    assert result["changed"] is True
    server_cfg = parsed["mcp_servers"]["chad-ui-playwright"]
    assert server_cfg["cwd"] == str(tmp_path / "src")
    assert server_cfg["env"]["PYTHONPATH"] == str(tmp_path / "src")


def test_ensure_global_mcp_config_dedupes_duplicate_blocks(tmp_path, monkeypatch):
    import tomllib

    from chad.mcp_config import ensure_global_mcp_config, _config_path

    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_path = _config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        '[mcp_servers.chad-ui-playwright]\n'
        'command = "python3"\n'
        'args = ["-m", "chad.mcp_playwright"]\n'
        'cwd = "/tmp/one"\n'
        'env = { PYTHONPATH = "/tmp/one/src" }\n\n'
        '[mcp_servers.chad-ui-playwright]\n'
        'command = "python3"\n'
        'args = ["-m", "chad.mcp_playwright"]\n'
        'cwd = "/tmp/two"\n'
        'env = { PYTHONPATH = "/tmp/two/src" }\n'
    )

    result = ensure_global_mcp_config(project_root=tmp_path)
    parsed = tomllib.loads(cfg_path.read_text())

    assert result["changed"] is True
    server_cfg = parsed["mcp_servers"]["chad-ui-playwright"]
    assert server_cfg["cwd"] == str(tmp_path / "src")
    assert server_cfg["env"]["PYTHONPATH"] == str(tmp_path / "src")


def test_alias_block_is_normalized(tmp_path, monkeypatch):
    import tomllib

    from chad.mcp_config import ensure_global_mcp_config, _config_path

    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_path = _config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        '[mcp_servers.chad-ui-playwright-main]\n'
        'command = "python3"\n'
        'args = ["-m", "chad.mcp_playwright"]\n'
        'cwd = "/tmp/alias"\n'
        'env = { PYTHONPATH = "/tmp/alias/src" }\n'
    )

    result = ensure_global_mcp_config(project_root=tmp_path)
    parsed = tomllib.loads(cfg_path.read_text())

    assert result["changed"] is True
    alias_cfg = parsed["mcp_servers"]["chad-ui-playwright-main"]
    assert alias_cfg["cwd"] == str(tmp_path / "src")
    assert alias_cfg["env"]["PYTHONPATH"] == str(tmp_path / "src")
