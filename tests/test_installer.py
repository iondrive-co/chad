"""Tests for installer and CLI resolution."""

from pathlib import Path

import chad.installer as installer


def _make_cli(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_ensure_tool_uses_existing_binary(tmp_path, monkeypatch):
    cli_path = _make_cli(tmp_path / "bin" / "codex")
    inst = installer.AIToolInstaller(tools_dir=tmp_path)
    monkeypatch.setattr(installer, "is_tool_installed", lambda name: False)

    success, resolved = inst.ensure_tool("codex")

    assert success is True
    assert resolved == str(cli_path)


def test_install_with_npm_when_missing(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, cwd=None):
        calls.append(cmd)
        _make_cli(tmp_path / "bin" / "codex")
        return 0, "ok", ""

    monkeypatch.setattr(installer, "run_command", fake_run)
    monkeypatch.setattr(installer, "is_tool_installed", lambda name: False)
    monkeypatch.setattr(installer.AIToolInstaller, "_check_node_npm", lambda self: True)
    inst = installer.AIToolInstaller(tools_dir=tmp_path)

    success, resolved = inst.ensure_tool("codex")

    assert success is True
    assert Path(resolved).exists()
    assert any(cmd and cmd[0] == "npm" for cmd in calls)


def test_install_with_npm_missing_node(tmp_path, monkeypatch):
    monkeypatch.setattr(installer.AIToolInstaller, "_check_node_npm", lambda self: False)
    monkeypatch.setattr(installer, "is_tool_installed", lambda name: False)
    inst = installer.AIToolInstaller(tools_dir=tmp_path)

    success, message = inst.ensure_tool("codex")

    assert success is False
    assert "Node" in message


def test_install_with_pip(tmp_path, monkeypatch):
    def fake_run(cmd, cwd=None):
        _make_cli(tmp_path / "bin" / "vibe")
        return 0, "", ""

    monkeypatch.setattr(installer, "run_command", fake_run)
    monkeypatch.setattr(installer, "is_tool_installed", lambda name: False)
    inst = installer.AIToolInstaller(tools_dir=tmp_path)

    success, resolved = inst.ensure_tool("vibe")

    assert success is True
    assert Path(resolved).exists()
