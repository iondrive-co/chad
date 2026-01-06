import os


def test_ensure_global_mcp_config_creates_file(tmp_path, monkeypatch):
    import tomllib

    from chad.mcp_config import ensure_global_mcp_config, _config_path

    # isolate HOME so we don't touch the real config
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_path = _config_path()
    result = ensure_global_mcp_config(project_root=tmp_path)

    assert result["changed"] is True
    assert cfg_path.exists()
    parsed = tomllib.loads(cfg_path.read_text())
    server_cfg = parsed["mcp_servers"]["chad-ui-playwright"]
    env_cfg = server_cfg["env"]
    assert server_cfg["cwd"] == str(tmp_path / "src")
    assert env_cfg["PYTHONPATH"] == str(tmp_path / "src")
    assert env_cfg["CHAD_PROJECT_ROOT"] == str(tmp_path)
    assert env_cfg["CHAD_PROJECT_ROOT_REASON"] == "argument"


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
    env_cfg = server_cfg["env"]
    assert server_cfg["cwd"] == str(tmp_path / "src")
    assert env_cfg["PYTHONPATH"] == str(tmp_path / "src")
    assert env_cfg["CHAD_PROJECT_ROOT"] == str(tmp_path)


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
    env_cfg = server_cfg["env"]
    assert server_cfg["cwd"] == str(tmp_path / "src")
    assert env_cfg["PYTHONPATH"] == str(tmp_path / "src")
    assert env_cfg["CHAD_PROJECT_ROOT"] == str(tmp_path)


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
    env_cfg = alias_cfg["env"]
    assert alias_cfg["cwd"] == str(tmp_path / "src")
    assert env_cfg["PYTHONPATH"] == str(tmp_path / "src")
    assert env_cfg["CHAD_PROJECT_ROOT"] == str(tmp_path)


def test_env_override_sets_project_root(tmp_path, monkeypatch):
    import tomllib

    from chad.mcp_config import ensure_global_mcp_config, _config_path

    alt_root = tmp_path / "override"
    alt_root.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CHAD_PROJECT_ROOT", str(alt_root))
    cfg_path = _config_path()

    result = ensure_global_mcp_config()
    parsed = tomllib.loads(cfg_path.read_text())
    server_cfg = parsed["mcp_servers"]["chad-ui-playwright"]
    env_cfg = server_cfg["env"]

    assert result["project_root"] == str(alt_root)
    assert env_cfg["CHAD_PROJECT_ROOT"] == str(alt_root)
    assert env_cfg["CHAD_PROJECT_ROOT_REASON"].startswith("env:")


def test_ensure_project_root_env_sets_env(monkeypatch, tmp_path):
    from chad.mcp_config import ensure_project_root_env

    monkeypatch.delenv("CHAD_PROJECT_ROOT", raising=False)
    result = ensure_project_root_env(tmp_path)

    assert result["changed"] is True
    assert os.environ["CHAD_PROJECT_ROOT"] == str(tmp_path)
    assert os.environ["CHAD_PROJECT_ROOT_REASON"] == "argument"


def test_ensure_project_root_env_respects_existing(monkeypatch, tmp_path):
    from chad.mcp_config import ensure_project_root_env

    # Use tmp_path for cross-platform compatibility (Unix paths normalize oddly on Windows)
    existing_path = str(tmp_path / "already" / "set")
    monkeypatch.setenv("CHAD_PROJECT_ROOT", existing_path)
    monkeypatch.delenv("CHAD_PROJECT_ROOT_REASON", raising=False)

    result = ensure_project_root_env(tmp_path)

    assert result["changed"] is True  # reason added
    assert os.environ["CHAD_PROJECT_ROOT"] == existing_path
    assert os.environ["CHAD_PROJECT_ROOT_REASON"].startswith("env:")
