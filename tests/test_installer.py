"""Tests for CLI installer behavior."""

from pathlib import Path

import sys
import types

from chad.util.installer import AIToolInstaller


def test_shell_installer_resolves_binary_from_user_install_dir(monkeypatch, tmp_path):
    """Shell installs should resolve binaries written under ~/.<tool>/bin."""
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True)

    installer = AIToolInstaller(tools_dir=tmp_path / "tools")
    spec = installer.tool_specs["opencode"]

    monkeypatch.setattr("chad.util.installer.Path.home", lambda: fake_home)

    def fake_urlretrieve(_url: str, script_path: str):
        Path(script_path).write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        return script_path, None

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(*_args, **_kwargs):
        binary_path = fake_home / ".opencode" / "bin" / "opencode"
        binary_path.parent.mkdir(parents=True, exist_ok=True)
        binary_path.write_text("#!/usr/bin/env bash\necho opencode\n", encoding="utf-8")
        return Completed()

    monkeypatch.setattr("urllib.request.urlretrieve", fake_urlretrieve)
    monkeypatch.setattr("subprocess.run", fake_run)

    ok, detail = installer._install_with_shell(spec)

    assert ok, detail
    resolved = Path(detail)
    assert resolved.exists()
    assert resolved.name == "opencode"


def test_cloudflared_installer_windows(monkeypatch, tmp_path):
    """Binary installer should pick Windows asset and emit .exe into bin dir."""
    installer = AIToolInstaller(tools_dir=tmp_path / "tools")

    # Pretend we're on Windows/AMD64 and no existing install is available
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setattr("chad.util.installer.is_tool_installed", lambda _b: False)

    def fake_urlretrieve(url, target):
        # Write a tiny placeholder exe
        Path(target).write_bytes(b"MZ")  # DOS header prefix
        return target, None

    monkeypatch.setattr("urllib.request.urlretrieve", fake_urlretrieve)

    ok, detail = installer.ensure_tool("cloudflared")

    assert ok, detail
    resolved = Path(detail)
    assert resolved.name == "cloudflared.exe"
    assert resolved.exists()


def test_cloudflared_download_failure_includes_manual_install_command(monkeypatch, tmp_path):
    """Binary installer failures should tell the user exactly how to install cloudflared."""
    installer = AIToolInstaller(tools_dir=tmp_path / "tools")
    spec = installer.tool_specs["cloudflared"]

    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("platform.machine", lambda: "x86_64")

    def fake_urlretrieve(_url, _target):
        raise OSError("network blocked")

    monkeypatch.setattr("urllib.request.urlretrieve", fake_urlretrieve)

    ok, detail = installer._install_binary(spec)

    assert not ok
    assert "Install it manually:" in detail
    assert f"mkdir -p {installer.bin_dir}" in detail
    assert "curl -fsSL" in detail
    assert str(installer.bin_dir / "cloudflared") in detail


def test_node_auto_install_for_npm_tools(monkeypatch, tmp_path):
    """When node/npm are missing, _install_with_npm auto-installs Node.js."""
    installer = AIToolInstaller(tools_dir=tmp_path / "tools")
    spec = installer.tool_specs["claude"]

    # Simulate node/npm not on PATH initially
    original_which = __import__("shutil").which

    def fake_which(name):
        # After _install_node adds node_dir/bin to PATH, node/npm become available
        node_bin = tmp_path / "tools" / "node" / "bin"
        if name in ("node", "npm") and str(node_bin) in __import__("os").environ.get("PATH", ""):
            return str(node_bin / name)
        if name in ("node", "npm"):
            return None
        return original_which(name)

    monkeypatch.setattr("shutil.which", fake_which)

    # Mock the download/extract to just create the node directory
    def fake_urlretrieve(url, target):
        Path(target).write_bytes(b"fake")
        return target, None

    monkeypatch.setattr("urllib.request.urlretrieve", fake_urlretrieve)

    class FakeTarFile:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def extractall(self, dest):
            # Simulate what the real tarball would produce
            node_bin = Path(dest) / "node-v22.16.0-linux-x64" / "bin"
            node_bin.mkdir(parents=True, exist_ok=True)
            (node_bin / "node").write_text("#!/bin/sh\n")
            (node_bin / "npm").write_text("#!/bin/sh\n")

    monkeypatch.setattr("tarfile.open", FakeTarFile)
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("platform.machine", lambda: "x86_64")

    # Mock npm install to succeed and create the binary
    def fake_run_command(cmd, cwd=None):
        if cmd[0] == "npm":
            npm_bin = installer.tools_dir / "node_modules" / ".bin"
            npm_bin.mkdir(parents=True, exist_ok=True)
            (npm_bin / "claude").write_text("#!/bin/sh\n")
            return 0, "", ""
        return 1, "", "unexpected command"

    monkeypatch.setattr("chad.util.installer.run_command", fake_run_command)

    ok, detail = installer._install_with_npm(spec)

    assert ok, detail
    assert "claude" in detail


def test_resolve_prefers_windows_suffix(monkeypatch, tmp_path):
    """resolve_tool_path should return .exe when both bare and .exe exist."""
    installer = AIToolInstaller(tools_dir=tmp_path / "tools")

    import os as real_os

    class FakeOS(types.SimpleNamespace):
        def __getattr__(self, item):
            return getattr(real_os, item)

    fake_os = FakeOS(name="nt")
    monkeypatch.setitem(sys.modules, "os", fake_os)

    bin_dir = installer.bin_dir
    bin_dir.mkdir(parents=True, exist_ok=True)

    exe = bin_dir / "cloudflared.exe"
    exe.write_bytes(b"MZ")
    bare = bin_dir / "cloudflared"
    bare.write_text("#!/bin/sh\n", encoding="utf-8")

    resolved = installer.resolve_tool_path("cloudflared")
    assert resolved == exe
