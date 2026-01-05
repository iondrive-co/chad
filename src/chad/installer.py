"""Install and resolve CLI tools for supported providers."""

from dataclasses import dataclass
from pathlib import Path
import sys

from .utils import ensure_directory, is_tool_installed, run_command


DEFAULT_TOOLS_DIR = Path.home() / ".chad" / "tools"


@dataclass(frozen=True)
class CLIToolSpec:
    """Metadata describing how to install a CLI tool."""

    name: str
    binary: str
    installer: str  # 'npm' or 'pip'
    package: str
    version: str | None = None

    @property
    def package_ref(self) -> str:
        return f"{self.package}@{self.version}" if self.version else self.package


class AIToolInstaller:
    """Handles installation of AI coding tools with per-user isolation."""

    def __init__(self, tools_dir: Path | None = None):
        self.tools_dir = tools_dir or DEFAULT_TOOLS_DIR
        self.bin_dir = self.tools_dir / "bin"
        ensure_directory(self.bin_dir)

        self.tool_specs: dict[str, CLIToolSpec] = {
            "codex": CLIToolSpec(
                name="Codex",
                binary="codex",
                installer="npm",
                package="@openai/codex",
                version="latest",
            ),
            "claude": CLIToolSpec(
                name="Claude",
                binary="claude",
                installer="npm",
                package="@anthropic-ai/claude-code",
                version="latest",
            ),
            "gemini": CLIToolSpec(
                name="Gemini",
                binary="gemini",
                installer="npm",
                package="@google/gemini-cli",
                version="latest",
            ),
            "vibe": CLIToolSpec(
                name="Mistral Vibe",
                binary="vibe",
                installer="pip",
                package="mistral-vibe",
                version=None,
            ),
        }

    def resolve_tool_path(self, binary: str) -> Path | None:
        """Return a path to the binary if it exists in tools dir or PATH."""
        import os

        candidate = self.bin_dir / binary
        if candidate.exists():
            return candidate

        # On Windows, npm creates .cmd wrappers
        if os.name == "nt":
            candidate_cmd = self.bin_dir / f"{binary}.cmd"
            if candidate_cmd.exists():
                return candidate_cmd

        npm_bin = self.tools_dir / "node_modules" / ".bin" / binary
        if npm_bin.exists():
            return npm_bin

        # On Windows, check for .cmd in npm bin
        if os.name == "nt":
            npm_bin_cmd = self.tools_dir / "node_modules" / ".bin" / f"{binary}.cmd"
            if npm_bin_cmd.exists():
                return npm_bin_cmd

        if is_tool_installed(binary):
            from shutil import which

            resolved = which(binary)
            return Path(resolved) if resolved else None
        return None

    def ensure_tool(self, tool_key: str) -> tuple[bool, str]:
        """Ensure the requested tool is installed. Returns (success, path|error)."""
        spec = self.tool_specs.get(tool_key)
        if not spec:
            return False, f"Unknown tool '{tool_key}'"

        existing = self.resolve_tool_path(spec.binary)
        if existing:
            return True, str(existing)

        if spec.installer == "npm":
            return self._install_with_npm(spec)
        if spec.installer == "pip":
            return self._install_with_pip(spec)
        return False, f"No installer configured for {spec.name}"

    def _install_with_npm(self, spec: CLIToolSpec) -> tuple[bool, str]:
        if not self._check_node_npm():
            return False, (
                f"Node.js and npm are required to install {spec.name}.\n\n"
                f"Please install Node.js from https://nodejs.org/ then try again.\n\n"
                f"Or install {spec.name} manually:\n"
                f"```\nnpm install -g {spec.package}\n```"
            )

        ensure_directory(self.tools_dir)
        ensure_directory(self.bin_dir)

        cmd = [
            "npm",
            "install",
            "--prefix",
            str(self.tools_dir),
            spec.package_ref,
        ]
        code, stdout, stderr = run_command(cmd)
        if code != 0:
            err = stderr.strip() or stdout.strip() or f"npm exited with code {code}"
            return False, (
                f"Failed to install {spec.name}: {err}\n\n"
                f"You can install it manually:\n"
                f"```\nnpm install -g {spec.package}\n```"
            )

        # Ensure a stable bin path by symlinking npm's .bin into our bin dir
        import os

        npm_bin = self.tools_dir / "node_modules" / ".bin" / spec.binary
        target_bin = self.bin_dir / spec.binary

        # On Windows, also handle .cmd wrappers
        if os.name == "nt":
            npm_bin_cmd = self.tools_dir / "node_modules" / ".bin" / f"{spec.binary}.cmd"
            target_bin_cmd = self.bin_dir / f"{spec.binary}.cmd"
            if npm_bin_cmd.exists() and not target_bin_cmd.exists():
                try:
                    target_bin_cmd.symlink_to(npm_bin_cmd)
                except (FileExistsError, OSError):
                    pass  # Symlink failed (needs admin on Windows), will use direct path

        if npm_bin.exists() and not target_bin.exists():
            try:
                target_bin.symlink_to(npm_bin)
            except (FileExistsError, OSError):
                pass  # Symlink failed (needs admin on Windows), will use direct path

        resolved = self.resolve_tool_path(spec.binary)
        if not resolved:
            return False, f"{spec.name} installation succeeded but '{spec.binary}' was not found."

        return True, str(resolved)

    def _install_with_pip(self, spec: CLIToolSpec) -> tuple[bool, str]:
        ensure_directory(self.tools_dir)
        ensure_directory(self.bin_dir)

        package_ref = f"{spec.package}=={spec.version}" if spec.version else spec.package
        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--prefix",
            str(self.tools_dir),
            package_ref,
        ]
        code, stdout, stderr = run_command(cmd)
        if code != 0:
            err = stderr.strip() or stdout.strip() or f"pip exited with code {code}"
            return False, f"pip install failed for {spec.name}: {err}"

        resolved = self.resolve_tool_path(spec.binary)
        if not resolved:
            return False, f"{spec.name} installation succeeded but '{spec.binary}' was not found."

        return True, str(resolved)

    def _check_node_npm(self) -> bool:
        return is_tool_installed("node") and is_tool_installed("npm")
