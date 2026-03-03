"""Tests for release packaging and installer generation."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# Path to the scripts directory
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
BUILD_RELEASE = SCRIPTS_DIR / "build_release.py"
PYINSTALLER_ENTRY = SCRIPTS_DIR / "pyinstaller_entry.py"


class TestPyInstallerEntry:
    """Tests for the PyInstaller entry point wrapper."""

    def test_entry_script_exists(self):
        """Verify pyinstaller_entry.py exists."""
        assert PYINSTALLER_ENTRY.exists(), (
            f"pyinstaller_entry.py not found at {PYINSTALLER_ENTRY}"
        )

    def test_entry_uses_absolute_imports(self):
        """Entry point must use absolute imports, not relative."""
        content = PYINSTALLER_ENTRY.read_text()
        assert "from chad.__main__ import main" in content
        assert "from ." not in content

    def test_entry_can_be_compiled(self):
        """Entry point should be valid Python that compiles without error."""
        content = PYINSTALLER_ENTRY.read_text()
        compile(content, str(PYINSTALLER_ENTRY), "exec")

    def test_entry_imports_main(self):
        """Entry point should successfully import chad.__main__.main."""
        from chad.__main__ import main
        assert callable(main)


class TestBuildReleaseScript:
    """Tests for the build_release.py script."""

    def test_script_exists(self):
        """Verify build_release.py script exists."""
        assert BUILD_RELEASE.exists(), f"build_release.py not found at {BUILD_RELEASE}"

    def test_script_can_be_imported(self):
        """Verify build_release.py can be imported without errors."""
        # Add scripts dir to path temporarily
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            import build_release
            # Should have required functions
            assert hasattr(build_release, "build_installer")
            assert hasattr(build_release, "get_platform_name")
            assert hasattr(build_release, "main")
        finally:
            sys.path.remove(str(SCRIPTS_DIR))

    def test_build_requires_modern_python(self, tmp_path):
        """build_installer should refuse to run on Python < 3.12."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            import build_release

            with patch.object(build_release.sys, "version_info", (3, 9)):
                with patch.object(build_release, "_find_project_python", return_value=None):
                    with pytest.raises(SystemExit):
                        build_release.build_installer(output_dir=tmp_path)
        finally:
            sys.path.remove(str(SCRIPTS_DIR))

    def test_require_min_python_reexecs_with_project_virtualenv(self):
        """_require_min_python should relaunch with a valid local venv when allowed."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            import build_release

            project_python = Path("/tmp/chad/.venv/bin/python3.12")
            with patch.object(build_release.sys, "version_info", (3, 11, 9)):
                with patch.object(build_release.sys, "executable", "/usr/bin/python3"):
                    with patch.object(build_release.sys, "argv", ["scripts/build_release.py", "--output", "/tmp/out"]):
                        with patch.dict(build_release.os.environ, {}, clear=True):
                            with patch.object(build_release, "_find_project_python", return_value=project_python):
                                with patch.object(build_release.os, "execve") as mock_execve:
                                    build_release._require_min_python(allow_reexec=True)

            mock_execve.assert_called_once_with(
                str(project_python),
                [str(project_python), str(BUILD_RELEASE), "--output", "/tmp/out"],
                {build_release.REEXEC_ENV_KEY: "1"},
            )
        finally:
            sys.path.remove(str(SCRIPTS_DIR))

    def test_require_min_python_shows_current_interpreter(self):
        """_require_min_python error should include interpreter path and version."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            import build_release

            with patch.object(build_release.sys, "version_info", (3, 11, 7)):
                with patch.object(build_release.sys, "executable", "/usr/bin/python3"):
                    with patch.object(build_release, "_find_project_python", return_value=None):
                        with pytest.raises(SystemExit) as exc:
                            build_release._require_min_python()

            message = str(exc.value)
            assert "Python 3.12+ required to build Chad." in message
            assert "Current interpreter: /usr/bin/python3 (3.11.7)." in message
        finally:
            sys.path.remove(str(SCRIPTS_DIR))

    def test_get_platform_name_linux(self):
        """Test platform detection for Linux."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            import build_release
            with patch("sys.platform", "linux"):
                assert build_release.get_platform_name() == "linux"
        finally:
            sys.path.remove(str(SCRIPTS_DIR))

    def test_get_platform_name_darwin(self):
        """Test platform detection for macOS."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            import build_release
            with patch("sys.platform", "darwin"):
                assert build_release.get_platform_name() == "macos"
        finally:
            sys.path.remove(str(SCRIPTS_DIR))

    def test_get_platform_name_windows(self):
        """Test platform detection for Windows."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            import build_release
            with patch("sys.platform", "win32"):
                assert build_release.get_platform_name() == "windows"
        finally:
            sys.path.remove(str(SCRIPTS_DIR))

    def test_ensure_pyinstaller_installs_when_missing(self):
        """PyInstaller should be installed automatically when not found."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            import build_release

            install_calls = []

            def fake_run_command(cmd, cwd=None):
                install_calls.append(cmd)

            with patch.object(build_release, "run_command", side_effect=fake_run_command):
                with patch("shutil.which", side_effect=[None, "/tmp/pyinstaller"]):
                    cmd = build_release.ensure_pyinstaller()

            assert cmd == ["/tmp/pyinstaller"]
            assert [sys.executable, "-m", "pip", "install", "pyinstaller"] in install_calls
        finally:
            sys.path.remove(str(SCRIPTS_DIR))

    def test_ensure_pyinstaller_falls_back_to_module_invocation(self):
        """If executable is still missing after install, fall back to python -m PyInstaller."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            import build_release
            import types

            sys.modules["PyInstaller"] = types.ModuleType("PyInstaller")
            with patch.object(build_release, "run_command"):
                with patch("shutil.which", side_effect=[None, None]):
                    cmd = build_release.ensure_pyinstaller()

            assert cmd == [sys.executable, "-m", "PyInstaller"]
        finally:
            sys.modules.pop("PyInstaller", None)
            sys.path.remove(str(SCRIPTS_DIR))

    def test_output_directory_creation(self, tmp_path):
        """Output directory should remain absent if PyInstaller install fails."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            import build_release
            output_dir = tmp_path / "dist" / "installers"

            with patch("shutil.which", return_value=None):
                with patch.object(
                    build_release,
                    "run_command",
                    side_effect=subprocess.CalledProcessError(1, ["pip"]),
                ):
                    with pytest.raises(subprocess.CalledProcessError):
                        build_release.build_installer(output_dir=output_dir)

            assert not output_dir.exists()
        finally:
            sys.path.remove(str(SCRIPTS_DIR))

    def test_build_installer_creates_output_directory(self, tmp_path):
        """Test that build_installer creates the output directory when pyinstaller exists."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            import build_release
            output_dir = tmp_path / "release" / "builds"

            # Create a mock artifact directory (PyInstaller onedir)
            mock_artifact_dir = tmp_path / "dist" / "chad"
            mock_artifact_dir.mkdir(parents=True)
            mock_artifact = mock_artifact_dir / "chad"
            mock_artifact.write_text("mock executable")

            # We need to patch at the module level where it's imported
            with patch("shutil.which", return_value="/usr/bin/pyinstaller"):
                with patch.object(build_release, "run_command"):
                    with patch.object(build_release, "build_ui"):
                        with patch.object(
                            build_release, "_find_built_artifact", return_value=mock_artifact
                        ):
                            with patch.object(
                                build_release, "_build_linux_deb", return_value=output_dir / "chad.deb"
                            ):
                                build_release.build_installer(output_dir=output_dir)

            # Output directory should be created
            assert output_dir.exists()
        finally:
            sys.path.remove(str(SCRIPTS_DIR))

    def test_version_extraction(self):
        """Test that version is correctly extracted from pyproject.toml."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            import build_release
            version = build_release.get_current_version()
            # Should match pattern like 0.11.0
            assert version is not None
            parts = version.split(".")
            assert len(parts) >= 2
            assert all(p.isdigit() for p in parts[:2])
        finally:
            sys.path.remove(str(SCRIPTS_DIR))

    def test_build_linux_deb_requires_dpkg(self, tmp_path):
        """_build_linux_deb should fail clearly if dpkg-deb is missing."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            import build_release
            artifact = tmp_path / "dist" / "chad" / "chad"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("bin")
            with patch("shutil.which", return_value=None):
                with pytest.raises(SystemExit):
                    build_release._build_linux_deb(artifact, "1.0.0", tmp_path)
        finally:
            sys.path.remove(str(SCRIPTS_DIR))

    def test_pyinstaller_uses_wrapper_entry_point(self, tmp_path):
        """PyInstaller should use pyinstaller_entry.py, not __main__.py directly."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            import build_release

            captured_args = []

            def capture_command(cmd, cwd=None):
                captured_args.append(cmd)

            mock_artifact = tmp_path / "dist" / "chad" / "chad"
            mock_artifact.parent.mkdir(parents=True)
            mock_artifact.write_text("mock")

            with patch("shutil.which", return_value="/usr/bin/pyinstaller"):
                with patch.object(build_release, "run_command", side_effect=capture_command):
                    with patch.object(build_release, "build_ui"):
                        with patch.object(
                            build_release, "_find_built_artifact", return_value=mock_artifact
                        ):
                            with patch.object(
                                build_release, "_build_linux_deb",
                                return_value=tmp_path / "chad.deb"
                            ):
                                build_release.build_installer(output_dir=tmp_path)

            # Find the PyInstaller invocation
            pyinstaller_call = [c for c in captured_args if "pyinstaller" in str(c[0]).lower()]
            assert pyinstaller_call, "PyInstaller should have been called"
            args = pyinstaller_call[0]

            # Should use pyinstaller_entry.py, NOT __main__.py
            entry_args = [a for a in args if "pyinstaller_entry" in str(a)]
            main_args = [a for a in args if "__main__" in str(a)]
            assert entry_args, "Should use pyinstaller_entry.py as entry point"
            assert not main_args, "Should NOT use __main__.py directly"

            # Should include --paths src
            assert "--paths" in args, "Should include --paths for chad package"

            # Should include --collect-submodules chad
            assert "--collect-submodules" in args, "Should collect chad submodules"
        finally:
            sys.path.remove(str(SCRIPTS_DIR))

    def test_add_data_separator_windows(self, tmp_path):
        """On Windows, --add-data separator should be semicolon, not colon."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            import build_release

            captured_args = []

            def capture_command(cmd, cwd=None):
                captured_args.append(cmd)

            mock_artifact = tmp_path / "dist" / "chad" / "chad.exe"
            mock_artifact.parent.mkdir(parents=True)
            mock_artifact.write_text("mock")

            with patch("sys.platform", "win32"):
                with patch("shutil.which", return_value="C:/pyinstaller.exe"):
                    with patch.object(build_release, "run_command", side_effect=capture_command):
                        with patch.object(build_release, "build_ui"):
                            with patch.object(
                                build_release, "_find_built_artifact", return_value=mock_artifact
                            ):
                                build_release.build_installer(output_dir=tmp_path)

            pyinstaller_call = [c for c in captured_args if "pyinstaller" in str(c[0]).lower()]
            assert pyinstaller_call
            args = pyinstaller_call[0]

            # Find --add-data argument
            add_data_idx = args.index("--add-data")
            add_data_val = args[add_data_idx + 1]
            assert ";" in add_data_val, (
                f"Windows --add-data should use ';' separator, got: {add_data_val}"
            )
        finally:
            sys.path.remove(str(SCRIPTS_DIR))

    def test_windows_builds_msi(self, tmp_path):
        """Windows build should produce an MSI via WiX tools."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            import build_release

            artifact_dir = tmp_path / "dist" / "chad"
            artifact_dir.mkdir(parents=True)
            artifact = artifact_dir / "chad.exe"
            artifact.write_text("bin")
            (artifact_dir / "_internal").mkdir()

            run_calls = []

            def fake_run_command(cmd, cwd=None):
                run_calls.append(cmd)

            tools = {
                "heat.exe": Path("C:/wix/heat.exe"),
                "candle.exe": Path("C:/wix/candle.exe"),
                "light.exe": Path("C:/wix/light.exe"),
            }

            version = build_release.get_current_version()

            with patch("sys.platform", "win32"):
                with patch("shutil.which", side_effect=lambda name: f"C:/{name}.exe"):
                    with patch.object(build_release, "_ensure_wix_tools", return_value=tools):
                        with patch.object(build_release, "run_command", side_effect=fake_run_command):
                            with patch.object(build_release, "build_ui"):
                                with patch.object(
                                    build_release, "_find_built_artifact", return_value=artifact
                                ):
                                    with patch.object(build_release.shutil, "rmtree"):
                                        final_path = build_release.build_installer(output_dir=tmp_path)

            assert final_path.suffix == ".msi"
            assert any(Path(c[0]).name == "heat.exe" for c in run_calls)
            assert any(Path(c[0]).name == "candle.exe" for c in run_calls)
            assert any(Path(c[0]).name == "light.exe" for c in run_calls)
            expected_staging = tmp_path / f"chad-{version}-msi"
            assert any(str(expected_staging / "harvest.wxs") in map(str, cmd) for cmd in run_calls)
        finally:
            sys.path.remove(str(SCRIPTS_DIR))

    def test_macos_builds_pkg_with_path_symlink(self, tmp_path):
        """macOS build should create a .pkg payload with /usr/local/bin/chad symlink."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            import build_release

            artifact_dir = tmp_path / "dist" / "chad"
            artifact_dir.mkdir(parents=True)
            artifact = artifact_dir / "chad"
            artifact.write_text("bin")
            (artifact_dir / "_internal").mkdir()

            run_calls = []

            def fake_run_command(cmd, cwd=None):
                run_calls.append(cmd)

            with patch("sys.platform", "darwin"):
                with patch("shutil.which", side_effect=lambda name: f"/usr/bin/{name}"):
                    with patch.object(build_release, "run_command", side_effect=fake_run_command):
                        with patch.object(build_release, "build_ui"):
                            with patch.object(
                                build_release, "_find_built_artifact", return_value=artifact
                            ):
                                with patch.object(build_release.shutil, "rmtree"):
                                    final_path = build_release.build_installer(output_dir=tmp_path)

            pkgbuild_calls = [c for c in run_calls if Path(c[0]).name == "pkgbuild"]
            assert pkgbuild_calls, "pkgbuild should be invoked to create the .pkg"
            pkg_cmd = pkgbuild_calls[-1]
            assert "--root" in pkg_cmd
            root_idx = pkg_cmd.index("--root")
            payload_root = Path(pkg_cmd[root_idx + 1])
            symlink_path = payload_root / "usr" / "local" / "bin" / "chad"
            assert symlink_path.is_symlink()
            assert symlink_path.readlink() == Path("../../../Applications/chad/chad")
            assert str(final_path) == pkg_cmd[-1]
            assert final_path.suffix == ".pkg"
        finally:
            sys.path.remove(str(SCRIPTS_DIR))


class TestGitHubActionsWorkflow:
    """Tests for the release GitHub Actions workflow."""

    WORKFLOW_PATH = Path(__file__).parent.parent / ".github" / "workflows" / "release.yml"

    def test_release_workflow_exists(self):
        """Verify release.yml workflow exists."""
        assert self.WORKFLOW_PATH.exists(), f"release.yml not found at {self.WORKFLOW_PATH}"

    def test_release_workflow_valid_yaml(self):
        """Verify release.yml is valid YAML."""
        import yaml
        content = self.WORKFLOW_PATH.read_text()
        # Should parse without error
        workflow = yaml.safe_load(content)
        assert workflow is not None
        assert "name" in workflow
        assert "jobs" in workflow

    def test_release_workflow_builds_all_platforms(self):
        """Verify release workflow builds for all platforms."""
        import yaml
        workflow = yaml.safe_load(self.WORKFLOW_PATH.read_text())

        # Should have a build job with matrix for platforms
        assert "jobs" in workflow
        assert "build" in workflow["jobs"]
        build_job = workflow["jobs"]["build"]

        # Check strategy matrix includes all platforms
        assert "strategy" in build_job
        assert "matrix" in build_job["strategy"]
        matrix = build_job["strategy"]["matrix"]

        # Should include Windows, macOS, and Linux
        if "os" in matrix:
            platforms = matrix["os"]
            platform_names = " ".join(platforms)
            assert "ubuntu" in platform_names or "linux" in platform_names.lower()
            assert "windows" in platform_names.lower()
            assert "macos" in platform_names.lower()

    def test_release_workflow_triggers_on_release(self):
        """Verify workflow triggers on release publish."""
        import yaml
        workflow = yaml.safe_load(self.WORKFLOW_PATH.read_text())

        assert "on" in workflow
        triggers = workflow["on"]
        # Should trigger on release
        assert "release" in triggers or "workflow_dispatch" in triggers

    def test_release_workflow_uploads_artifacts(self):
        """Verify workflow uploads artifacts."""
        import yaml
        workflow = yaml.safe_load(self.WORKFLOW_PATH.read_text())

        # Find upload step in build job
        build_job = workflow["jobs"]["build"]
        steps = build_job.get("steps", [])

        has_upload = any(
            "upload" in str(step.get("uses", "")).lower() or
            "upload" in str(step.get("name", "")).lower()
            for step in steps
        )
        assert has_upload, "Workflow should upload artifacts"


class TestPyCharmRunConfiguration:
    """Tests for PyCharm run configuration."""

    CONFIG_PATH = Path(__file__).parent.parent / ".idea" / "runConfigurations" / "Build_Release.xml"

    def test_run_config_exists(self):
        """Verify PyCharm run configuration exists."""
        assert self.CONFIG_PATH.exists(), f"Build_Release.xml not found at {self.CONFIG_PATH}"

    def test_run_config_valid_xml(self):
        """Verify run configuration is valid XML."""
        import xml.etree.ElementTree as ET
        # Should parse without error
        tree = ET.parse(self.CONFIG_PATH)
        root = tree.getroot()
        assert root.tag == "component"

    def test_run_config_targets_build_release_script(self):
        """Verify run configuration targets the build_release.py script."""
        content = self.CONFIG_PATH.read_text()
        assert "build_release.py" in content


class TestInstallerNaming:
    """Tests for installer naming conventions."""

    def test_installer_filename_format(self):
        """Test installer filename follows expected format."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            import build_release

            # Test Linux
            with patch("sys.platform", "linux"):
                name = build_release.get_installer_filename("0.11.0")
                assert name == "chad-0.11.0-linux.deb"

            # Test macOS
            with patch("sys.platform", "darwin"):
                name = build_release.get_installer_filename("0.11.0")
                assert name == "chad-0.11.0-macos.pkg"

            # Test Windows
            with patch("sys.platform", "win32"):
                name = build_release.get_installer_filename("0.11.0")
                assert name == "chad-0.11.0-windows.msi"
        finally:
            sys.path.remove(str(SCRIPTS_DIR))
