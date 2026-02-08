"""Tests for project_setup module."""

import json
import os
import subprocess

import pytest

from chad.util.project_setup import (
    detect_project_type,
    detect_verification_commands,
    detect_python_executable,
    detect_doc_paths,
    ensure_docs_config,
    validate_command,
    load_project_config,
    save_project_config,
    save_project_settings,
    setup_project,
    build_verification_instructions,
    build_doc_reference_text,
    ProjectConfig,
    VerificationConfig,
)


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Create an isolated config file for testing project settings."""
    config_path = tmp_path / "chad_test.conf"
    monkeypatch.setenv("CHAD_CONFIG", str(config_path))
    return config_path


class TestDetectProjectType:
    """Test cases for detect_project_type."""

    def test_detect_python_pyproject(self, tmp_path):
        """Test detection of Python project via pyproject.toml."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n")
        assert detect_project_type(tmp_path) == "python"

    def test_detect_python_setup_py(self, tmp_path):
        """Test detection of Python project via setup.py."""
        (tmp_path / "setup.py").write_text("from setuptools import setup\nsetup()")
        assert detect_project_type(tmp_path) == "python"

    def test_detect_python_requirements(self, tmp_path):
        """Test detection of Python project via requirements.txt."""
        (tmp_path / "requirements.txt").write_text("flask\nrequests\n")
        assert detect_project_type(tmp_path) == "python"

    def test_detect_javascript(self, tmp_path):
        """Test detection of JavaScript project via package.json."""
        (tmp_path / "package.json").write_text('{"name": "test"}')
        assert detect_project_type(tmp_path) == "javascript"

    def test_detect_typescript(self, tmp_path):
        """Test detection of TypeScript project via tsconfig.json."""
        (tmp_path / "tsconfig.json").write_text('{"compilerOptions": {}}')
        assert detect_project_type(tmp_path) == "typescript"

    def test_detect_rust(self, tmp_path):
        """Test detection of Rust project via Cargo.toml."""
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'test'\n")
        assert detect_project_type(tmp_path) == "rust"

    def test_detect_go(self, tmp_path):
        """Test detection of Go project via go.mod."""
        (tmp_path / "go.mod").write_text("module test\n")
        assert detect_project_type(tmp_path) == "go"

    def test_detect_unknown(self, tmp_path):
        """Test detection of unknown project type."""
        assert detect_project_type(tmp_path) == "unknown"


class TestDetectPythonExecutable:
    """Test cases for detect_python_executable."""

    def test_detect_venv_bin(self, tmp_path):
        """Test detection of .venv/bin/python."""
        venv_python = tmp_path / ".venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("#!/usr/bin/env python3")
        assert detect_python_executable(tmp_path) == str(venv_python)

    def test_detect_venv_scripts(self, tmp_path):
        """Test detection of .venv/Scripts/python.exe (Windows style)."""
        venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("stub")
        assert detect_python_executable(tmp_path) == str(venv_python)

    def test_fallback_to_python3(self, tmp_path):
        """Test fallback to python3 when no venv found."""
        assert detect_python_executable(tmp_path) == "python3"


class TestDetectVerificationCommands:
    """Test cases for detect_verification_commands."""

    def test_detect_python_commands(self, tmp_path):
        """Test detection of Python verification commands."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "tests").mkdir()

        result = detect_verification_commands(tmp_path)
        assert result["project_type"] == "python"
        assert "flake8" in result["lint_command"]
        assert "pytest" in result["test_command"]

    def test_detect_makefile_commands(self, tmp_path):
        """Test detection of Makefile targets."""
        (tmp_path / "Makefile").write_text("lint:\n\tflake8 .\n\ntest:\n\tpytest\n")
        (tmp_path / "pyproject.toml").write_text("[project]\n")

        result = detect_verification_commands(tmp_path)
        assert result["lint_command"] == "make lint"
        assert result["test_command"] == "make test"

    def test_detect_javascript_commands(self, tmp_path):
        """Test detection of JavaScript verification commands."""
        package_json = {"name": "test", "scripts": {"lint": "eslint .", "test": "jest"}}
        (tmp_path / "package.json").write_text(json.dumps(package_json))

        result = detect_verification_commands(tmp_path)
        assert result["project_type"] == "javascript"
        assert result["lint_command"] == "npm run lint"
        assert result["test_command"] == "npm test"

    def test_javascript_missing_scripts(self, tmp_path):
        """Test JavaScript project without lint/test scripts."""
        package_json = {"name": "test"}  # No scripts
        (tmp_path / "package.json").write_text(json.dumps(package_json))

        result = detect_verification_commands(tmp_path)
        assert result["lint_command"] is None
        assert result["test_command"] is None


class TestValidateCommand:
    """Test cases for validate_command."""

    def test_validate_successful_command(self, tmp_path):
        """Test validation of a successful command."""
        success, output = validate_command("echo hello", tmp_path, timeout=5)
        assert success is True
        assert "hello" in output

    def test_validate_failing_command(self, tmp_path):
        """Test validation of a failing command."""
        success, output = validate_command("exit 1", tmp_path, timeout=5)
        assert success is False

    def test_validate_nonexistent_command(self, tmp_path):
        """Test validation of a non-existent command."""
        success, output = validate_command("nonexistent_command_xyz", tmp_path, timeout=5)
        assert success is False


class TestProjectConfig:
    """Test cases for ProjectConfig save/load."""

    def test_save_and_load_config(self, tmp_path, isolated_config):
        """Test saving and loading project configuration."""
        project_path = tmp_path / "my_project"
        project_path.mkdir()

        config = ProjectConfig(
            project_type="python",
            verification=VerificationConfig(
                lint_command="flake8 .",
                test_command="pytest tests/",
                validated=True,
            ),
        )

        save_project_config(project_path, config)

        # Verify config was saved to main config file
        assert isolated_config.exists()

        # Load and verify
        loaded = load_project_config(project_path)
        assert loaded is not None
        assert loaded.project_type == "python"
        assert loaded.verification.lint_command == "flake8 ."
        assert loaded.verification.test_command == "pytest tests/"
        assert loaded.verification.validated is True

    def test_load_nonexistent_config(self, tmp_path, isolated_config):
        """Test loading config when it doesn't exist."""
        result = load_project_config(tmp_path)
        assert result is None

    def test_config_to_dict(self):
        """Test ProjectConfig serialization to dict."""
        config = ProjectConfig(
            project_type="rust",
            verification=VerificationConfig(
                lint_command="cargo clippy",
                test_command="cargo test",
            ),
        )
        data = config.to_dict()

        assert data["project_type"] == "rust"
        assert data["verification"]["lint_command"] == "cargo clippy"
        assert data["verification"]["test_command"] == "cargo test"

    def test_config_from_dict(self):
        """Test ProjectConfig deserialization from dict."""
        data = {
            "version": "1.0",
            "project_type": "go",
            "verification": {
                "lint_command": "golint ./...",
                "test_command": "go test ./...",
                "lint_timeout": 30,
                "test_timeout": 120,
                "validated": True,
            },
        }
        config = ProjectConfig.from_dict(data)

        assert config.project_type == "go"
        assert config.verification.lint_command == "golint ./..."
        assert config.verification.validated is True


class TestSetupProject:
    """Test cases for setup_project."""

    def test_setup_new_project(self, tmp_path, isolated_config):
        """Test setting up a new project with detection."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "tests").mkdir()

        # Run setup without validation (validation would try to run commands)
        config = setup_project(tmp_path, validate=False)

        assert config.project_type == "python"
        assert "flake8" in config.verification.lint_command
        assert "pytest" in config.verification.test_command

    def test_setup_loads_existing_config(self, tmp_path, isolated_config):
        """Test that setup loads existing validated config."""
        existing_config = ProjectConfig(
            project_type="custom",
            verification=VerificationConfig(
                lint_command="custom_lint",
                test_command="custom_test",
                validated=True,
            ),
        )
        save_project_config(tmp_path, existing_config)

        # Should return existing config
        config = setup_project(tmp_path, validate=False)
        assert config.verification.lint_command == "custom_lint"


class TestBuildVerificationInstructions:
    """Test cases for build_verification_instructions."""

    def test_instructions_with_validated_config(self, tmp_path, isolated_config):
        """Test building instructions from validated config."""
        config = ProjectConfig(
            project_type="python",
            verification=VerificationConfig(
                lint_command="flake8 src/",
                test_command="pytest tests/",
                validated=True,
            ),
        )
        save_project_config(tmp_path, config)

        instructions = build_verification_instructions(tmp_path)
        assert "flake8 src/" in instructions
        assert "pytest tests/" in instructions

    def test_instructions_with_auto_detection(self, tmp_path, isolated_config):
        """Test building instructions via auto-detection."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "tests").mkdir()

        instructions = build_verification_instructions(tmp_path)
        assert "flake8" in instructions
        assert "pytest" in instructions

    def test_instructions_unknown_project(self, tmp_path, isolated_config):
        """Test building instructions for unknown project type."""
        instructions = build_verification_instructions(tmp_path)
        assert "verification patterns" in instructions.lower()
        assert "pytest" in instructions  # Generic hints


class TestDocsConfig:
    """Test detection and prompt reference generation for docs."""

    def test_detect_doc_paths_prefers_agents_and_architecture(self, tmp_path):
        """Detect doc paths and store them relative to project root."""
        (tmp_path / "AGENTS.md").write_text("instructions", encoding="utf-8")
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "ARCHITECTURE.md").write_text("arch", encoding="utf-8")

        docs = detect_doc_paths(tmp_path)
        assert docs.instructions_path == "AGENTS.md"
        assert docs.architecture_path == "docs/ARCHITECTURE.md"

    def test_ensure_docs_config_persists_to_config_file(self, tmp_path, isolated_config):
        """ensure_docs_config should save discovered paths into main config file."""
        (tmp_path / "AGENTS.md").write_text("instructions", encoding="utf-8")
        docs = ensure_docs_config(tmp_path)
        assert docs.instructions_path == "AGENTS.md"

        saved = load_project_config(tmp_path)
        assert saved is not None
        assert saved.docs.instructions_path == "AGENTS.md"

    def test_build_doc_reference_text_outputs_absolute_paths(self, tmp_path, isolated_config):
        """Doc reference text should point to absolute file locations."""
        (tmp_path / "AGENTS.md").write_text("instructions", encoding="utf-8")
        ref_text = build_doc_reference_text(tmp_path)
        assert ref_text is not None
        assert str(tmp_path / "AGENTS.md") in ref_text

    def test_save_project_settings_respects_custom_doc_paths(self, tmp_path, isolated_config):
        """Custom doc paths saved via project settings should be used in prompts."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        custom_instructions = docs_dir / "DEV_GUIDE.md"
        custom_architecture = tmp_path / "ARCH.md"
        custom_instructions.write_text("custom instructions", encoding="utf-8")
        custom_architecture.write_text("arch overview", encoding="utf-8")

        saved = save_project_settings(
            tmp_path,
            lint_command="flake8 .",
            test_command="pytest -q",
            instructions_path="docs/DEV_GUIDE.md",
            architecture_path="ARCH.md",
        )

        assert saved.docs.instructions_path == "docs/DEV_GUIDE.md"
        assert saved.docs.architecture_path == "ARCH.md"

        ref_text = build_doc_reference_text(tmp_path)
        assert str(custom_instructions.resolve()) in ref_text
        assert str(custom_architecture.resolve()) in ref_text

    def test_build_doc_reference_text_uses_saved_doc_paths_in_worktree(self, tmp_path, isolated_config):
        """Worktree prompt docs should use saved project doc paths, not auto-detected defaults."""
        from chad.util.git_worktree import GitWorktreeManager

        (tmp_path / "AGENTS.md").write_text("default instructions", encoding="utf-8")
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        custom_instructions = docs_dir / "DEV_GUIDE.md"
        custom_instructions.write_text("custom instructions", encoding="utf-8")
        (tmp_path / "README.md").write_text("repo", encoding="utf-8")

        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True, text=True)
        commit_env = os.environ.copy()
        commit_env.update(
            {
                "GIT_AUTHOR_NAME": "test",
                "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "test",
                "GIT_COMMITTER_EMAIL": "test@example.com",
            }
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
            env=commit_env,
        )

        save_project_settings(
            tmp_path,
            lint_command="flake8 .",
            test_command="pytest -q",
            instructions_path="docs/DEV_GUIDE.md",
            architecture_path=None,
        )

        manager = GitWorktreeManager(tmp_path)
        worktree_path, _ = manager.create_worktree("docscheck")
        worktree_path = worktree_path.resolve()
        try:
            ref_text = build_doc_reference_text(worktree_path)
            assert ref_text is not None
            assert str(worktree_path / "docs" / "DEV_GUIDE.md") in ref_text
            assert str(worktree_path / "AGENTS.md") not in ref_text
        finally:
            subprocess.run(
                ["git", "worktree", "remove", str(worktree_path), "--force"],
                cwd=tmp_path,
                check=False,
                capture_output=True,
                text=True,
            )
