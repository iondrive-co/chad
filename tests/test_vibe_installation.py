"""Tests for Mistral Vibe pip installation."""

from pathlib import Path
import sys

from chad.util.installer import AIToolInstaller


def test_vibe_pip_installation_missing_module(monkeypatch, tmp_path):
    """Test that vibe installation properly installs the Python module."""
    installer = AIToolInstaller(tools_dir=tmp_path / "tools")

    # Mock run_command to simulate pip install success but module not on path
    def mock_run_command(cmd):
        if "pip" in cmd and "install" in cmd:
            # Create a fake vibe script in the expected location
            bin_dir = tmp_path / "tools" / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            vibe_script = bin_dir / "vibe"
            vibe_script.write_text("""#!/usr/bin/env python
# This simulates the installed vibe script
from vibe.cli.entrypoint import main
main()
""")
            vibe_script.chmod(0o755)
            return 0, "Successfully installed mistral-vibe", ""
        return 1, "", "Command failed"

    monkeypatch.setattr("chad.util.installer.run_command", mock_run_command)
    monkeypatch.setattr("chad.util.installer.is_tool_installed", lambda x: False)

    success, path = installer.ensure_tool("vibe")
    assert success
    assert "vibe" in path
    assert Path(path).exists()


def test_vibe_installation_with_existing_global_binary(monkeypatch, tmp_path):
    """Test that existing global vibe binary is used without modification."""
    # Create a fake global vibe that works
    global_bin_dir = tmp_path / "global_bin"
    global_bin_dir.mkdir(parents=True)
    global_vibe = global_bin_dir / "vibe"
    global_vibe.write_text("""#!/usr/bin/env python
# Global vibe installation
print("Global vibe")
""")
    global_vibe.chmod(0o755)

    installer = AIToolInstaller(tools_dir=tmp_path / "tools")

    # Mock which to return our global vibe
    def mock_which(cmd):
        if cmd == "vibe":
            return str(global_vibe)
        return None

    monkeypatch.setattr("shutil.which", mock_which)
    monkeypatch.setattr("chad.util.installer.is_tool_installed", lambda x: x == "vibe")

    success, path = installer.ensure_tool("vibe")
    assert success
    assert path == str(global_vibe)
    # Ensure the global binary wasn't modified
    assert global_vibe.read_text() == """#!/usr/bin/env python
# Global vibe installation
print("Global vibe")
"""


def test_vibe_pip_install_with_proper_pythonpath(monkeypatch, tmp_path):
    """Test that pip installation sets up proper Python paths for the vibe module."""
    installer = AIToolInstaller(tools_dir=tmp_path / "tools")

    # Track the pip install command
    pip_commands = []

    def mock_run_command(cmd):
        pip_commands.append(cmd)
        if "pip" in cmd and "install" in cmd:
            # Simulate pip installing to the prefix directory
            site_packages = tmp_path / "tools" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
            site_packages.mkdir(parents=True, exist_ok=True)

            # Create mock vibe module
            vibe_module = site_packages / "vibe"
            vibe_module.mkdir(parents=True, exist_ok=True)
            (vibe_module / "__init__.py").touch()

            cli_module = vibe_module / "cli"
            cli_module.mkdir(exist_ok=True)
            (cli_module / "__init__.py").touch()
            (cli_module / "entrypoint.py").write_text("def main(): print('Vibe CLI')")

            # Create the vibe script that pip would create
            bin_dir = tmp_path / "tools" / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            vibe_script = bin_dir / "vibe"

            # Write a proper entry point script that adds the site-packages to sys.path
            vibe_script.write_text(f"""#!/usr/bin/env python
import sys
sys.path.insert(0, r'{site_packages}')
from vibe.cli.entrypoint import main
if __name__ == '__main__':
    main()
""")
            vibe_script.chmod(0o755)

            return 0, "Successfully installed mistral-vibe", ""
        return 1, "", "Command failed"

    monkeypatch.setattr("chad.util.installer.run_command", mock_run_command)
    monkeypatch.setattr("chad.util.installer.is_tool_installed", lambda x: False)

    success, path = installer.ensure_tool("vibe")
    assert success
    assert len(pip_commands) == 1
    assert "--prefix" in pip_commands[0]
    assert str(tmp_path / "tools") in pip_commands[0]

    # Verify the script exists and has proper content
    script = Path(path)
    assert script.exists()
    content = script.read_text()
    assert "sys.path.insert" in content
    assert "site-packages" in content


def test_vibe_repairs_broken_shebang(monkeypatch, tmp_path):
    """Existing vibe scripts with dead shebangs should be repaired automatically."""

    installer = AIToolInstaller(tools_dir=tmp_path / "tools")

    # Create the expected site-packages directory so the path fix is applied
    site_packages = (
        tmp_path
        / "tools"
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    site_packages.mkdir(parents=True, exist_ok=True)

    bin_dir = tmp_path / "tools" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    # Simulate a stale pip script pointing at a removed virtualenv
    orig_script = bin_dir / "vibe.orig"
    orig_script.write_text("""#!/tmp/vanished/.venv/bin/python
print('broken vibe')
""")
    orig_script.chmod(0o755)

    # Bash wrapper that execs the broken script (matches the user's observed state)
    wrapper = bin_dir / "vibe"
    wrapper.write_text(f"#!/usr/bin/env bash\nexec \"{orig_script}\" \"$@\"\n")
    wrapper.chmod(0o755)

    # Ensure we don't fall back to a global binary
    monkeypatch.setattr("chad.util.installer.is_tool_installed", lambda _x: False)

    ok, path = installer.ensure_tool("vibe")

    assert ok
    assert Path(path) == wrapper

    repaired_lines = orig_script.read_text().splitlines()
    assert repaired_lines[0].strip() == "#!/usr/bin/env python3"
    assert any("CHAD_PYTHONPATH_FIX" in line for line in repaired_lines)


def test_vibe_installation_permission_error_handling(monkeypatch, tmp_path):
    """Test that permission errors are handled gracefully."""
    # Create a read-only directory to simulate permission issues
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    readonly_vibe = readonly_dir / "vibe"
    readonly_vibe.write_text("#!/usr/bin/env python\nprint('readonly')")
    readonly_vibe.chmod(0o444)  # Read-only
    readonly_dir.chmod(0o555)  # Read-only directory

    installer = AIToolInstaller(tools_dir=tmp_path / "tools")

    def mock_run_command(cmd):
        if "pip" in cmd:
            return 1, "", "PermissionError: [Errno 13] Permission denied"
        return 1, "", "Command failed"

    monkeypatch.setattr("chad.util.installer.run_command", mock_run_command)
    monkeypatch.setattr("chad.util.installer.is_tool_installed", lambda x: False)

    success, error = installer.ensure_tool("vibe")
    assert not success
    assert "pip install failed" in error
    assert "PermissionError" in error or "Permission denied" in error

    # Cleanup
    readonly_dir.chmod(0o755)
    readonly_vibe.chmod(0o644)
