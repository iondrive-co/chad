"""Tests for CLI installer behavior."""

from pathlib import Path

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
