"""Project setup detection and configuration for Chad.

This module handles detecting project type, verification commands,
and persisting project-specific configuration in the main chad config file.
"""

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class VerificationConfig:
    """Configuration for project verification commands."""

    lint_command: str | None = None
    test_command: str | None = None
    lint_timeout: int = 30
    test_timeout: int = 120
    validated: bool = False
    last_validated: str | None = None


@dataclass
class DocsConfig:
    """Configuration for project documentation locations."""

    instructions_paths: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "DocsConfig":
        """Create from dictionary, migrating old single-path fields."""
        paths = data.get("instructions_paths")
        if paths is not None:
            return cls(instructions_paths=list(paths))
        # Migrate legacy single-field format
        migrated: list[str] = []
        old_inst = data.get("instructions_path")
        old_arch = data.get("architecture_path")
        if old_inst:
            migrated.append(old_inst)
        if old_arch:
            migrated.append(old_arch)
        return cls(instructions_paths=migrated)


@dataclass
class ProjectConfig:
    """Project configuration stored in the main chad config file."""

    version: str = "1.0"
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    project_type: str = "unknown"
    verification: VerificationConfig = field(default_factory=VerificationConfig)
    instructions: str | None = None
    docs: DocsConfig = field(default_factory=DocsConfig)
    preview_port: int | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "version": self.version,
            "detected_at": self.detected_at,
            "project_type": self.project_type,
            "verification": {
                "lint_command": self.verification.lint_command,
                "test_command": self.verification.test_command,
                "lint_timeout": self.verification.lint_timeout,
                "test_timeout": self.verification.test_timeout,
                "validated": self.verification.validated,
                "last_validated": self.verification.last_validated,
            },
            "instructions": self.instructions,
            "docs": {
                "instructions_paths": self.docs.instructions_paths,
            },
            "preview_port": self.preview_port,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectConfig":
        """Create from dictionary."""
        verification_data = data.get("verification", {})
        verification = VerificationConfig(
            lint_command=verification_data.get("lint_command"),
            test_command=verification_data.get("test_command"),
            lint_timeout=verification_data.get("lint_timeout", 30),
            test_timeout=verification_data.get("test_timeout", 120),
            validated=verification_data.get("validated", False),
            last_validated=verification_data.get("last_validated"),
        )
        docs_data = data.get("docs", {})
        docs = DocsConfig.from_dict(docs_data)
        return cls(
            version=data.get("version", "1.0"),
            detected_at=data.get("detected_at", ""),
            project_type=data.get("project_type", "unknown"),
            verification=verification,
            instructions=data.get("instructions"),
            docs=docs,
            preview_port=data.get("preview_port"),
        )


# Detection heuristics for project types
PROJECT_DETECTION_RULES = {
    "python": {
        "files": ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"],
        "lint_command": "{python} -m flake8 .",
        "test_command": "{python} -m pytest tests/ -v",
    },
    "javascript": {
        "files": ["package.json"],
        "lint_command": "npm run lint",
        "test_command": "npm test",
    },
    "typescript": {
        "files": ["tsconfig.json"],
        "lint_command": "npm run lint",
        "test_command": "npm test",
    },
    "rust": {
        "files": ["Cargo.toml"],
        "lint_command": "cargo clippy",
        "test_command": "cargo test",
    },
    "go": {
        "files": ["go.mod"],
        "lint_command": "golint ./...",
        "test_command": "go test ./...",
    },
}

INSTRUCTION_DOC_CANDIDATES = [
    "AGENTS.md",
    ".claude/CLAUDE.md",
    "CLAUDE.md",
]

ARCHITECTURE_DOC_CANDIDATES = [
    "docs/ARCHITECTURE.md",
    "docs/architecture.md",
    "ARCHITECTURE.md",
    "architecture.md",
    "docs/ARCH.md",
    "ARCH.md",
]


def detect_python_executable(project_path: Path) -> str:
    """Detect the Python executable for a project.

    Checks for common virtual environment locations and falls back to python3.
    """
    venv_paths = [
        project_path / ".venv" / "bin" / "python",
        project_path / ".venv" / "Scripts" / "python.exe",
        project_path / "venv" / "bin" / "python",
        project_path / "venv" / "Scripts" / "python.exe",
    ]

    for venv_path in venv_paths:
        if venv_path.exists():
            return str(venv_path)

    return "python3"


def detect_project_type(project_path: Path) -> str:
    """Detect the project type based on configuration files.

    Args:
        project_path: Path to the project root

    Returns:
        Project type string: "python", "javascript", "typescript", "rust", "go", or "unknown"
    """
    project_path = Path(project_path)

    # Check for Makefile with common targets first
    makefile = project_path / "Makefile"
    if makefile.exists():
        try:
            content = makefile.read_text(encoding="utf-8")
            if "test:" in content or "lint:" in content:
                # Still detect the underlying project type
                pass
        except (OSError, UnicodeDecodeError):
            pass

    # Check each project type's detection files
    for project_type, rules in PROJECT_DETECTION_RULES.items():
        for filename in rules["files"]:
            if (project_path / filename).exists():
                return project_type

    return "unknown"


def detect_verification_commands(project_path: Path) -> dict:
    """Auto-detect lint and test commands based on project files.

    Args:
        project_path: Path to the project root

    Returns:
        Dictionary with 'lint_command' and 'test_command' keys (may be None)
    """
    project_path = Path(project_path)
    project_type = detect_project_type(project_path)

    lint_command = None
    test_command = None

    # Check for Makefile with test/lint targets first
    makefile = project_path / "Makefile"
    if makefile.exists():
        try:
            content = makefile.read_text(encoding="utf-8")
            if "lint:" in content:
                lint_command = "make lint"
            if "test:" in content:
                test_command = "make test"
        except (OSError, UnicodeDecodeError):
            pass

    # Use project-type-specific commands if Makefile doesn't have them
    if project_type in PROJECT_DETECTION_RULES:
        rules = PROJECT_DETECTION_RULES[project_type]

        if not lint_command and rules.get("lint_command"):
            lint_cmd = rules["lint_command"]
            if "{python}" in lint_cmd:
                python = detect_python_executable(project_path)
                lint_cmd = lint_cmd.replace("{python}", python)
            lint_command = lint_cmd

        if not test_command and rules.get("test_command"):
            test_cmd = rules["test_command"]
            if "{python}" in test_cmd:
                python = detect_python_executable(project_path)
                test_cmd = test_cmd.replace("{python}", python)
            test_command = test_cmd

    # For Python projects, check for specific test directories
    if project_type == "python" and test_command:
        if not (project_path / "tests").exists():
            # Try alternative test locations
            if (project_path / "test").exists():
                test_command = test_command.replace("tests/", "test/")
            elif (project_path / "src").exists():
                # pytest can discover tests without specifying path
                python = detect_python_executable(project_path)
                test_command = f"{python} -m pytest -v"

    # For JavaScript/TypeScript, check if lint script exists in package.json
    if project_type in ("javascript", "typescript"):
        package_json = project_path / "package.json"
        if package_json.exists():
            try:
                data = json.loads(package_json.read_text(encoding="utf-8"))
                scripts = data.get("scripts", {})
                if "lint" not in scripts:
                    lint_command = None
                if "test" not in scripts:
                    test_command = None
            except (json.JSONDecodeError, OSError):
                pass

    return {
        "lint_command": lint_command,
        "test_command": test_command,
        "project_type": project_type,
    }


DOC_CANDIDATES = INSTRUCTION_DOC_CANDIDATES + ARCHITECTURE_DOC_CANDIDATES


def detect_doc_paths(project_path: Path) -> DocsConfig:
    """Find instruction and architecture docs in the project."""
    project_path = Path(project_path)
    found: list[str] = []
    for candidate in DOC_CANDIDATES:
        candidate_path = project_path / candidate
        if candidate_path.exists():
            rel = str(candidate_path.relative_to(project_path))
            if rel not in found:
                found.append(rel)

    return DocsConfig(instructions_paths=found)


def _get_config_project_root(project_path: Path) -> Path:
    """Resolve the canonical project root for config lookup.

    Worktrees live under `<repo>/.chad-worktrees/<session_id>`. Project
    settings are saved for the main repo root, not each ephemeral worktree.
    """
    project_path = Path(project_path).resolve()
    if project_path.parent.name == ".chad-worktrees":
        return project_path.parent.parent
    return project_path


def ensure_docs_config(project_path: Path) -> DocsConfig:
    """Ensure docs paths are recorded in project config and return them."""
    project_path = Path(project_path).resolve()
    config_root = _get_config_project_root(project_path)
    config = load_project_config(config_root)

    if config is None:
        config = ProjectConfig()

    docs = config.docs or DocsConfig()
    detected = detect_doc_paths(config_root)

    if not docs.instructions_paths:
        docs.instructions_paths = detected.instructions_paths

    config.docs = docs
    save_project_config(config_root, config)

    return docs


def build_doc_reference_text(project_path: Path) -> str | None:
    """Build a short reference section pointing to on-disk docs."""
    project_path = Path(project_path).resolve()
    docs = ensure_docs_config(project_path)

    if not docs.instructions_paths:
        return None

    lines: list[str] = []
    for p in docs.instructions_paths:
        lines.append(f"- {project_path / p}")

    return "Read the following project files from disk before making changes:\n" + "\n".join(lines)


def validate_command(
    command: str,
    project_path: Path,
    timeout: int = 30,
) -> tuple[bool, str]:
    """Run a command and check if it succeeds.

    Args:
        command: Command to run
        project_path: Working directory
        timeout: Timeout in seconds

    Returns:
        Tuple of (success: bool, output: str)
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output.strip()
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout} seconds"
    except Exception as e:
        return False, str(e)


def load_project_config(project_path: Path) -> ProjectConfig | None:
    """Load project configuration from the main chad config file.

    Args:
        project_path: Path to the project root

    Returns:
        ProjectConfig if config exists, None otherwise
    """
    from chad.util.config_manager import ConfigManager

    config_manager = ConfigManager()
    data = config_manager.get_project_config(project_path)

    if data is None:
        return None

    try:
        return ProjectConfig.from_dict(data)
    except (KeyError, TypeError):
        return None


def save_project_config(project_path: Path, config: ProjectConfig) -> None:
    """Save project configuration to the main chad config file.

    Args:
        project_path: Path to the project root
        config: ProjectConfig to save
    """
    from chad.util.config_manager import ConfigManager

    config_manager = ConfigManager()
    config_manager.set_project_config(project_path, config.to_dict())


def save_project_settings(
    project_path: Path,
    lint_command: str | None = None,
    test_command: str | None = None,
    instructions_paths: list[str] | None = None,
    preview_port: int | None = ...,
) -> ProjectConfig:
    """Persist verification commands and documentation paths for a project.

    Args:
        project_path: Path to the project root
        lint_command: Lint command to save (None to clear)
        test_command: Test command to save (None to clear)
        instructions_paths: List of paths to agent instruction/doc files
        preview_port: Local port for preview tunnel (None to clear, ... to leave unchanged)

    Returns:
        The saved ProjectConfig instance
    """

    project_path = Path(project_path).expanduser().resolve()

    config = load_project_config(project_path)
    if config is None:
        detected = detect_verification_commands(project_path)
        config = ProjectConfig(
            project_type=detected["project_type"],
            verification=VerificationConfig(
                lint_command=detected.get("lint_command"),
                test_command=detected.get("test_command"),
            ),
        )

    # Always refresh project type so the label stays accurate
    config.project_type = detect_project_type(project_path)

    # Update verification commands and mark them validated (user provided)
    config.verification.lint_command = lint_command or None
    config.verification.test_command = test_command or None
    config.verification.validated = True

    docs = config.docs or DocsConfig()

    if instructions_paths is not None:
        docs.instructions_paths = [p.strip() for p in instructions_paths if p.strip()]

    if not docs.instructions_paths:
        detected_docs = detect_doc_paths(project_path)
        docs.instructions_paths = detected_docs.instructions_paths

    config.docs = docs

    if preview_port is not ...:
        config.preview_port = preview_port

    save_project_config(project_path, config)
    return config


def setup_project(project_path: Path, validate: bool = True) -> ProjectConfig:
    """Detect and optionally validate verification commands for a project.

    This is the main entry point for project setup. It:
    1. Loads existing config if present
    2. Detects project type and commands if not configured
    3. Optionally validates the detected commands
    4. Saves the configuration

    Args:
        project_path: Path to the project root
        validate: Whether to run validation on detected commands

    Returns:
        ProjectConfig with detected/configured settings
    """
    project_path = Path(project_path)

    # Load existing config if present
    config = load_project_config(project_path)
    if config and config.verification.validated:
        return config

    # Detect project type and commands
    detected = detect_verification_commands(project_path)

    if config is None:
        config = ProjectConfig(
            project_type=detected["project_type"],
            verification=VerificationConfig(
                lint_command=detected["lint_command"],
                test_command=detected["test_command"],
            ),
        )
    else:
        # Update with detected values if not already set
        if not config.verification.lint_command:
            config.verification.lint_command = detected["lint_command"]
        if not config.verification.test_command:
            config.verification.test_command = detected["test_command"]
        if config.project_type == "unknown":
            config.project_type = detected["project_type"]

    # Detect documentation paths
    detected_docs = detect_doc_paths(project_path)
    if not config.docs.instructions_paths:
        config.docs.instructions_paths = detected_docs.instructions_paths

    # Validate commands if requested
    if validate:
        all_valid = True

        if config.verification.lint_command:
            lint_ok, _ = validate_command(
                config.verification.lint_command,
                project_path,
                config.verification.lint_timeout,
            )
            if not lint_ok:
                all_valid = False

        if config.verification.test_command:
            test_ok, _ = validate_command(
                config.verification.test_command,
                project_path,
                config.verification.test_timeout,
            )
            if not test_ok:
                all_valid = False

        config.verification.validated = all_valid
        if all_valid:
            config.verification.last_validated = datetime.now(timezone.utc).isoformat()

    # Save configuration
    save_project_config(project_path, config)

    return config


def build_verification_instructions(project_path: Path) -> str:
    """Build verification instructions for the coding agent prompt.

    Returns verification instructions based on:
    1. Saved project config if exists and validated
    2. Auto-detected commands if no config
    3. Generic fallback

    Args:
        project_path: Path to the project root

    Returns:
        Verification instructions string for the coding agent prompt
    """
    project_path = Path(project_path)

    # Try to load project config
    config = load_project_config(project_path)

    lint_cmd = None
    test_cmd = None

    if config and config.verification.validated:
        lint_cmd = config.verification.lint_command
        test_cmd = config.verification.test_command
    else:
        # Auto-detect commands
        detected = detect_verification_commands(project_path)
        lint_cmd = detected.get("lint_command")
        test_cmd = detected.get("test_command")

    # Build instructions
    instructions = []
    instructions.append("## Verification")
    instructions.append("")
    instructions.append("Before completing your task, run verification to ensure your changes don't break anything.")
    instructions.append("")

    if lint_cmd or test_cmd:
        if lint_cmd:
            instructions.append(f"**Lint command:** `{lint_cmd}`")
        if test_cmd:
            instructions.append(f"**Test command:** `{test_cmd}`")
        instructions.append("")
        instructions.append("Run both commands and fix any issues before completing the task.")
    else:
        instructions.append("Look for common verification patterns in this project:")
        instructions.append("- Python: `python -m pytest tests/` and `python -m flake8`")
        instructions.append("- JavaScript/TypeScript: `npm test` and `npm run lint`")
        instructions.append("- Rust: `cargo test` and `cargo clippy`")
        instructions.append("- Go: `go test ./...` and `golint ./...`")
        instructions.append("- Makefile: `make test` and `make lint`")
        instructions.append("")
        instructions.append("Check the project's README or CI configuration for the correct commands.")

    return "\n".join(instructions)
