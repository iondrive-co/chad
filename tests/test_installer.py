"""Tests for CLI installer behavior."""

from pathlib import Path

import pytest

import sys
import types

from chad.util.installer import AIToolInstaller


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

    class FakeMember:
        def __init__(self, name):
            self.name = name

        def islnk(self):
            return False

        def issym(self):
            return False

        def isfile(self):
            return True

        def isdir(self):
            return False

    class FakeTarFile:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def getmembers(self):
            # Members are validated before extraction (path-traversal guard)
            return [FakeMember("node-v22.16.0-linux-x64/bin/node"),
                    FakeMember("node-v22.16.0-linux-x64/bin/npm")]

        def extractall(self, dest, filter=None):
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


class TestManagedToolUpdates:
    """Installed provider CLIs must not rot.

    ``ensure_tool`` returns the moment a binary exists, so a CLI installed once
    stayed at that version forever: Chad was still running Claude Code 2.1.12
    seven months later, which turned an expired token into a 3-minute retry
    stall instead of a 1-second "log in again". Staleness is refreshed on a
    timer, away from the task path.
    """

    def _installed(self, tmp_path, tool="claude"):
        installer = AIToolInstaller(tools_dir=tmp_path / "tools")
        npm_bin = installer.tools_dir / "node_modules" / ".bin"
        npm_bin.mkdir(parents=True, exist_ok=True)
        (npm_bin / tool).write_text("#!/bin/sh\n", encoding="utf-8")
        return installer

    def test_ensure_tool_never_shells_out_when_already_installed(self, monkeypatch, tmp_path):
        """The task path must stay fast — no npm on the way to spawning an agent."""
        installer = self._installed(tmp_path)
        monkeypatch.setattr(
            "chad.util.installer.run_command",
            lambda *a, **k: pytest.fail("ensure_tool must not install over an existing tool"),
        )

        ok, detail = installer.ensure_tool("claude")

        assert ok
        assert detail.endswith("claude")

    def test_stale_tool_is_reinstalled_at_latest(self, monkeypatch, tmp_path):
        installer = self._installed(tmp_path)
        # Only node/npm come from PATH; real provider CLIs on this machine must
        # not make the temp tools dir look populated.
        monkeypatch.setattr(
            "chad.util.installer.is_tool_installed", lambda b: b in ("node", "npm")
        )
        commands = []
        monkeypatch.setattr(
            "chad.util.installer.run_command",
            lambda cmd, cwd=None: (commands.append(cmd), (0, "", ""))[1],
        )

        updated = installer.update_stale_tools(max_age_days=7)

        assert "claude" in updated
        assert any("@anthropic-ai/claude-code@latest" in " ".join(c) for c in commands)

    def test_recently_updated_tool_is_left_alone(self, monkeypatch, tmp_path):
        installer = self._installed(tmp_path)
        # Only node/npm come from PATH; real provider CLIs on this machine must
        # not make the temp tools dir look populated.
        monkeypatch.setattr(
            "chad.util.installer.is_tool_installed", lambda b: b in ("node", "npm")
        )
        monkeypatch.setattr("chad.util.installer.run_command", lambda *a, **k: (0, "", ""))

        assert installer.update_stale_tools(max_age_days=7) == ["claude"]

        monkeypatch.setattr(
            "chad.util.installer.run_command",
            lambda *a, **k: pytest.fail("a freshly updated tool must not be reinstalled"),
        )
        assert installer.update_stale_tools(max_age_days=7) == []

    def test_tools_that_were_never_installed_are_not_installed_by_the_updater(
        self, monkeypatch, tmp_path
    ):
        """Updating refreshes what the user has; it doesn't install the whole catalog."""
        installer = AIToolInstaller(tools_dir=tmp_path / "tools")
        monkeypatch.setattr("chad.util.installer.is_tool_installed", lambda _b: False)
        monkeypatch.setattr(
            "chad.util.installer.run_command",
            lambda *a, **k: pytest.fail("nothing is installed, so nothing to update"),
        )

        assert installer.update_stale_tools(max_age_days=7) == []

    def test_pip_tool_install_cannot_strip_chad_own_environment(self, monkeypatch, tmp_path):
        """A prefixed pip install must not uninstall from the running interpreter.

        pip resolves against the active site-packages even with --prefix, so
        upgrading a pip-installed CLI moved shared dependencies out of Chad's
        virtualenv and into the tools prefix — breaking Chad itself.
        """
        installer = AIToolInstaller(tools_dir=tmp_path / "tools")
        commands = []
        monkeypatch.setattr(
            "chad.util.installer.run_command",
            lambda cmd, cwd=None: (commands.append(cmd), (0, "", ""))[1],
        )

        installer._install_with_pip(installer.tool_specs["vibe"])

        assert commands, "expected a pip invocation"
        assert "--ignore-installed" in commands[0]
        assert "--prefix" in commands[0]

    def test_a_cli_the_user_installed_themselves_is_left_alone(self, monkeypatch, tmp_path):
        """Only Chad's own installs are refreshed — a PATH binary isn't ours.

        This also keeps test runs and temp-home launches inert: with an empty
        tools dir, the real CLIs on PATH would otherwise all look installable.
        """
        installer = AIToolInstaller(tools_dir=tmp_path / "tools")
        user_bin = tmp_path / "usr-local-bin"
        user_bin.mkdir()
        (user_bin / "claude").write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setattr("chad.util.installer.is_tool_installed", lambda b: b == "claude")
        monkeypatch.setattr("shutil.which", lambda b: str(user_bin / b) if b == "claude" else None)
        monkeypatch.setattr(
            "chad.util.installer.run_command",
            lambda *a, **k: pytest.fail("must not npm-install over a user's own CLI"),
        )

        assert installer.update_stale_tools(max_age_days=7) == []

    def test_failed_update_keeps_the_working_install_and_retries_later(
        self, monkeypatch, tmp_path
    ):
        """A broken network must not stamp the tool as fresh, nor break the CLI."""
        installer = self._installed(tmp_path)
        # Only node/npm come from PATH; real provider CLIs on this machine must
        # not make the temp tools dir look populated.
        monkeypatch.setattr(
            "chad.util.installer.is_tool_installed", lambda b: b in ("node", "npm")
        )
        monkeypatch.setattr(
            "chad.util.installer.run_command", lambda *a, **k: (1, "", "network down")
        )

        assert installer.update_stale_tools(max_age_days=7) == []
        assert installer.resolve_tool_path("claude") is not None

        calls = []
        monkeypatch.setattr(
            "chad.util.installer.run_command",
            lambda cmd, cwd=None: (calls.append(cmd), (0, "", ""))[1],
        )
        assert installer.update_stale_tools(max_age_days=7) == ["claude"]
        assert calls, "a failed update must be retried on the next check"


class TestArchiveExtractionSafety:
    """Downloaded archives are untrusted — members must not escape tools_dir."""

    def test_rejects_traversal_member_names(self):
        from chad.util.installer import _is_unsafe_member_name

        for bad in ("../evil", "/etc/passwd", "a/../../b", "\\windows\\system32"):
            assert _is_unsafe_member_name(bad), bad
        for good in ("node/bin/node", "pkg/lib/x.js"):
            assert not _is_unsafe_member_name(good), good

    def test_zip_guard_raises_on_unsafe_member(self):
        from chad.util.installer import _assert_safe_zip_members

        class FakeZip:
            def namelist(self):
                return ["ok/file", "../escape"]

        with pytest.raises(ValueError, match="unsafe archive member"):
            _assert_safe_zip_members(FakeZip())

    def test_tar_guard_rejects_escaping_symlink(self):
        from chad.util.installer import _assert_safe_tar_members

        class Member:
            def __init__(self, name, linkname="", sym=False):
                self.name = name
                self.linkname = linkname
                self._sym = sym

            def islnk(self):
                return False

            def issym(self):
                return self._sym

            def isfile(self):
                return not self._sym

            def isdir(self):
                return False

        class FakeTar:
            def getmembers(self):
                return [Member("pkg/link", "../../etc/passwd", sym=True)]

        with pytest.raises(ValueError, match="escaping"):
            _assert_safe_tar_members(FakeTar())
