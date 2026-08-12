"""Install and resolve CLI tools for supported providers."""

from dataclasses import dataclass
from pathlib import Path
import json
import sys
import time

from .utils import ensure_directory, is_tool_installed, run_command


DEFAULT_TOOLS_DIR = Path.home() / ".chad" / "tools"


def _is_unsafe_member_name(name: str) -> bool:
    """True when an archive member would extract outside the target dir."""
    pure = Path(name)
    return pure.is_absolute() or ".." in pure.parts or name.startswith(("/", "\\"))


def _assert_safe_zip_members(zf) -> None:
    """Reject zip members that escape the extraction directory."""
    for name in zf.namelist():
        if _is_unsafe_member_name(name):
            raise ValueError(f"Refusing to extract unsafe archive member: {name}")


def _assert_safe_tar_members(tf) -> None:
    """Reject tar members that escape the directory or aren't file/dir/symlink."""
    for member in tf.getmembers():
        if _is_unsafe_member_name(member.name):
            raise ValueError(f"Refusing to extract unsafe archive member: {member.name}")
        if member.islnk() or member.issym():
            target = member.linkname
            if _is_unsafe_member_name(target):
                raise ValueError(
                    f"Refusing to extract link escaping the archive: {member.name}"
                )
        elif not (member.isfile() or member.isdir()):
            raise ValueError(f"Refusing to extract special archive member: {member.name}")


@dataclass(frozen=True)
class CLIToolSpec:
    """Metadata describing how to install a CLI tool."""

    name: str
    binary: str
    installer: str  # 'npm', 'pip', or 'binary'
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
            "qwen": CLIToolSpec(
                name="Qwen Code",
                binary="qwen",
                installer="npm",
                package="@qwen-code/qwen-code",
                version="latest",
            ),
            "vibe": CLIToolSpec(
                name="Mistral Vibe",
                binary="vibe",
                installer="pip",
                package="mistral-vibe",
                version=None,
            ),
            "kimi": CLIToolSpec(
                name="Kimi Code",
                binary="kimi",
                installer="pip",
                package="kimi-cli",
                version=None,
            ),
            "cloudflared": CLIToolSpec(
                name="Cloudflared",
                binary="cloudflared",
                installer="binary",
                package="https://github.com/cloudflare/cloudflared/releases/latest/download",
                version=None,
            ),
        }

    def resolve_tool_path(self, binary: str) -> Path | None:
        """Return a path to the binary if it exists in tools dir or PATH."""
        import os

        if os.name == "nt":
            # pip --prefix installs console scripts into Scripts/, not bin/,
            # so a successful install used to resolve to "not found"
            scripts_dir = self.tools_dir / "Scripts"
            candidates = [
                self.bin_dir / f"{binary}.exe",
                self.bin_dir / f"{binary}.cmd",
                self.bin_dir / binary,
                scripts_dir / f"{binary}.exe",
                scripts_dir / f"{binary}.cmd",
                scripts_dir / binary,
            ]
        else:
            candidates = [
                self.bin_dir / binary,
                self.bin_dir / f"{binary}.exe",
                self.bin_dir / f"{binary}.cmd",
            ]

        for candidate in candidates:
            if candidate.exists():
                return candidate

        npm_bin = self.tools_dir / "node_modules" / ".bin" / binary
        if npm_bin.exists():
            return npm_bin

        if os.name == "nt":
            npm_bin_cmd = self.tools_dir / "node_modules" / ".bin" / f"{binary}.cmd"
            npm_bin_exe = self.tools_dir / "node_modules" / ".bin" / f"{binary}.exe"
            if npm_bin_cmd.exists():
                return npm_bin_cmd
            if npm_bin_exe.exists():
                return npm_bin_exe

        if is_tool_installed(binary):
            from shutil import which

            resolved = which(binary)
            return Path(resolved) if resolved else None
        return None

    def ensure_tool(self, tool_key: str) -> tuple[bool, str]:
        """Ensure the requested tool is installed. Returns (success, path|error).

        Deliberately does no version check: this runs on the way to spawning an
        agent, and must not put an npm round-trip in front of the user's task.
        Keeping installs current is ``update_stale_tools``'s job.
        """
        spec = self.tool_specs.get(tool_key)
        if not spec:
            return False, f"Unknown tool '{tool_key}'"

        existing = self.resolve_tool_path(spec.binary)
        if existing:
            if tool_key == "vibe":
                self._repair_vibe_install(Path(existing))
            return True, str(existing)

        return self._install(spec)

    def _install(self, spec: CLIToolSpec) -> tuple[bool, str]:
        if spec.installer == "npm":
            return self._install_with_npm(spec)
        if spec.installer == "pip":
            return self._install_with_pip(spec)
        if spec.installer == "binary":
            return self._install_binary(spec)
        return False, f"No installer configured for {spec.name}"

    @property
    def _update_stamp_file(self) -> Path:
        return self.tools_dir / "last-update.json"

    def _read_update_stamps(self) -> dict[str, float]:
        try:
            data = json.loads(self._update_stamp_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, (int, float))}

    def _write_update_stamp(self, tool_key: str, when: float) -> None:
        stamps = self._read_update_stamps()
        stamps[tool_key] = when
        try:
            ensure_directory(self.tools_dir)
            self._update_stamp_file.write_text(json.dumps(stamps, indent=2), encoding="utf-8")
        except OSError:
            pass  # A missing stamp only means we check again next time

    def _is_managed_install(self, resolved: Path | None) -> bool:
        """True when this path is a CLI Chad installed into its own tools dir."""
        if resolved is None:
            return False
        tools_dir = str(self.tools_dir)
        return str(resolved).startswith(tools_dir) or str(resolved.resolve()).startswith(tools_dir)

    def update_stale_tools(self, max_age_days: int = 7) -> list[str]:
        """Reinstall installed provider CLIs that haven't been refreshed lately.

        ``ensure_tool`` returns as soon as a binary exists, so without this a CLI
        installed once was frozen at that version indefinitely — Chad was still
        running a Claude Code build seven months old, which turned an expired
        login into a three-minute retry stall rather than an instant error.

        Only CLIs Chad installed itself are touched — a binary the user put on
        PATH is theirs to manage — and a failed update leaves both the working
        install and the old stamp alone so it retries later. Returns the tool
        keys that were updated.
        """
        import logging

        log = logging.getLogger("chad.installer")
        now = time.time()
        max_age_seconds = max_age_days * 86400
        stamps = self._read_update_stamps()
        updated: list[str] = []

        for tool_key, spec in self.tool_specs.items():
            if spec.installer not in ("npm", "pip"):
                continue
            if not self._is_managed_install(self.resolve_tool_path(spec.binary)):
                continue  # Not ours: absent, or the user's own install on PATH
            last = stamps.get(tool_key)
            if last is not None and now - last < max_age_seconds:
                continue

            ok, detail = self._install(spec)
            if ok:
                self._write_update_stamp(tool_key, now)
                updated.append(tool_key)
                log.info("Updated %s to the latest release", spec.name)
            else:
                log.warning("Could not update %s: %s", spec.name, detail)

        return updated

    def _binary_asset(self, spec: CLIToolSpec) -> tuple[str, str]:
        """Return the download URL and output filename for a binary tool."""
        import platform

        system = platform.system().lower()
        machine = platform.machine().lower()

        if system == "darwin":
            os_name = "darwin"
            ext = ""
        elif system == "linux":
            os_name = "linux"
            ext = ""
        elif system == "windows":
            os_name = "windows"
            ext = ".exe"
        else:
            raise ValueError(f"Unsupported platform: {system}")

        if machine in ("x86_64", "amd64"):
            arch = "amd64"
        elif machine in ("aarch64", "arm64"):
            arch = "arm64"
        else:
            raise ValueError(f"Unsupported architecture: {machine}")

        asset = f"{spec.binary}-{os_name}-{arch}{ext}"
        url = f"{spec.package}/{asset}"
        target_name = f"{spec.binary}{ext}" if ext else spec.binary
        return url, target_name

    def _manual_binary_install_command(self, url: str, target_name: str) -> str:
        """Return an exact manual install command for the current platform."""
        target = self.bin_dir / target_name

        if target_name.endswith(".exe"):
            return (
                f'powershell -NoProfile -Command '
                f'"New-Item -ItemType Directory -Force -Path \'{self.bin_dir}\' | Out-Null; '
                f'Invoke-WebRequest -Uri \'{url}\' -OutFile \'{target}\'"'
            )

        return (
            f"mkdir -p {self.bin_dir} && "
            f"curl -fsSL {url} -o {target} && "
            f"chmod +x {target}"
        )

    def _install_with_npm(self, spec: CLIToolSpec) -> tuple[bool, str]:
        if not self._check_node_npm():
            ok, err = self._install_node()
            if not ok:
                return False, (
                    f"Node.js is required to install {spec.name} but auto-install failed:\n"
                    f"{err}\n\n"
                    f"Please install Node.js manually from https://nodejs.org/ then try again."
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
            # --prefix does not stop pip from resolving against the running
            # interpreter's site-packages: without this, upgrading a tool
            # *uninstalls* shared dependencies from Chad's own environment and
            # re-installs them under the prefix, leaving Chad broken. Isolate
            # the install completely.
            "--ignore-installed",
            "--prefix",
            str(self.tools_dir),
            package_ref,
        ]
        code, stdout, stderr = run_command(cmd)
        if code != 0:
            err = stderr.strip() or stdout.strip() or f"pip exited with code {code}"
            return False, f"pip install failed for {spec.name}: {err}"

        # After pip install, we need to ensure the binary script has proper PYTHONPATH
        # Pip installs packages to <prefix>/lib/pythonX.Y/site-packages
        resolved = self.resolve_tool_path(spec.binary)
        if resolved:
            # Check if it's a pip-installed script in our managed directory
            if str(resolved).startswith(str(self.tools_dir)):
                self._fix_pip_script_pythonpath(resolved)
            return True, str(resolved)

        return False, f"{spec.name} installation succeeded but '{spec.binary}' was not found."

    def _check_node_npm(self) -> bool:
        return is_tool_installed("node") and is_tool_installed("npm")

    def _install_node(self) -> tuple[bool, str]:
        """Install a local Node.js into the managed tools directory."""
        import logging
        import platform
        import tarfile
        import urllib.request
        import zipfile

        log = logging.getLogger("chad.installer")
        node_dir = self.tools_dir / "node"

        # Already installed locally in a previous run?
        local_node = node_dir / "bin" / "node"
        local_npm = node_dir / "bin" / "npm"
        if local_node.exists() and local_npm.exists():
            self._add_node_to_path(node_dir)
            return True, str(local_node)

        system = platform.system().lower()
        machine = platform.machine().lower()

        if machine in ("x86_64", "amd64"):
            arch = "x64"
        elif machine in ("aarch64", "arm64"):
            arch = "arm64"
        else:
            return False, f"Unsupported architecture: {machine}"

        node_version = "v22.16.0"

        if system == "linux":
            archive = f"node-{node_version}-linux-{arch}.tar.xz"
        elif system == "darwin":
            archive = f"node-{node_version}-darwin-{arch}.tar.gz"
        elif system == "windows":
            archive = f"node-{node_version}-win-{arch}.zip"
        else:
            return False, f"Unsupported platform: {system}"

        url = f"https://nodejs.org/dist/{node_version}/{archive}"
        download_path = self.tools_dir / archive

        log.info("Downloading Node.js %s for %s/%s...", node_version, system, arch)
        try:
            urllib.request.urlretrieve(url, str(download_path))
        except Exception as e:
            return False, f"Failed to download Node.js: {e}"

        log.info("Extracting Node.js...")
        try:
            ensure_directory(node_dir)

            # Downloaded archives are untrusted input: refuse members that
            # would escape tools_dir (path traversal / absolute paths) and
            # anything that isn't a regular file or directory.
            if archive.endswith(".zip"):
                with zipfile.ZipFile(download_path) as zf:
                    _assert_safe_zip_members(zf)
                    zf.extractall(self.tools_dir)
            elif archive.endswith((".tar.xz", ".tar.gz")):
                mode = "r:xz" if archive.endswith(".tar.xz") else "r:gz"
                with tarfile.open(download_path, mode) as tf:
                    _assert_safe_tar_members(tf)
                    # data filter also strips setuid bits / odd member types
                    tf.extractall(self.tools_dir, filter="data")

            # The archive extracts to a versioned directory — rename to "node"
            extracted_name = archive.replace(".tar.xz", "").replace(".tar.gz", "").replace(".zip", "")
            extracted_dir = self.tools_dir / extracted_name
            if extracted_dir.exists() and extracted_dir != node_dir:
                if node_dir.exists():
                    import shutil
                    shutil.rmtree(node_dir)
                extracted_dir.rename(node_dir)
        except Exception as e:
            return False, f"Failed to extract Node.js: {e}"
        finally:
            try:
                download_path.unlink(missing_ok=True)
            except OSError:
                pass

        self._add_node_to_path(node_dir)

        # Verify it works
        if not self._check_node_npm():
            return False, "Node.js was extracted but node/npm not found on PATH"

        log.info("Node.js %s installed to %s", node_version, node_dir)
        return True, str(local_node)

    def _add_node_to_path(self, node_dir: Path) -> None:
        """Add the managed Node.js bin directory to PATH."""
        import os

        bin_dir = str(node_dir / "bin")
        path = os.environ.get("PATH", "")
        if bin_dir not in path.split(os.pathsep):
            os.environ["PATH"] = bin_dir + os.pathsep + path

    def _install_binary(self, spec: CLIToolSpec) -> tuple[bool, str]:
        """Install a tool by downloading a platform-appropriate binary."""
        import stat
        import urllib.request

        ensure_directory(self.tools_dir)
        ensure_directory(self.bin_dir)

        try:
            url, target_name = self._binary_asset(spec)
        except ValueError as e:
            return False, str(e)

        target = self.bin_dir / target_name

        try:
            urllib.request.urlretrieve(url, str(target))
            if not target_name.endswith(".exe"):
                target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except Exception as e:
            return False, (
                f"Failed to download {spec.name}: {e}\n\n"
                f"Install it manually:\n"
                f"{self._manual_binary_install_command(url, target_name)}"
            )

        resolved = self.resolve_tool_path(spec.binary)
        if not resolved:
            return False, f"{spec.name} download succeeded but '{spec.binary}' was not found."

        return True, str(resolved)

    def _fix_pip_script_pythonpath(self, script_path: Path) -> None:
        """Ensure pip-installed scripts can find their modules."""
        import stat

        # Calculate the site-packages directory where pip installed the modules
        python_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
        site_packages = self.tools_dir / "lib" / python_version / "site-packages"

        if not site_packages.exists():
            return

        try:
            # Read the current script
            content = script_path.read_text()

            # Check if it already has our path fix
            if "# CHAD_PYTHONPATH_FIX" in content:
                return

            # Find the shebang line
            lines = content.splitlines(keepends=True)
            if not lines or not lines[0].startswith("#!"):
                return

            # Insert our path fix after the shebang
            path_fix = f"""# CHAD_PYTHONPATH_FIX
import sys
sys.path.insert(0, r'{site_packages}')
"""
            new_lines = [lines[0], path_fix] + lines[1:]
            new_content = "".join(new_lines)

            # Write the modified script
            script_path.write_text(new_content)

            # Ensure it remains executable
            current_mode = script_path.stat().st_mode
            script_path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        except (OSError, IOError):
            # If we can't modify the script, continue anyway
            pass

    def _normalize_python_shebang(self, script_path: Path) -> None:
        """Rewrite shebang to a stable python3 interpreter for managed scripts."""
        import stat

        if not script_path.exists():
            return

        resolved_path = script_path.resolve()
        if not str(script_path).startswith(str(self.tools_dir)) and not str(resolved_path).startswith(str(self.tools_dir)):
            return  # Only touch managed installs

        try:
            lines = script_path.read_text().splitlines()
        except (OSError, UnicodeDecodeError):
            return

        if not lines:
            return

        lines[0] = "#!/usr/bin/env python3"
        new_content = "\n".join(lines)
        if not new_content.endswith("\n"):
            new_content += "\n"

        try:
            script_path.write_text(new_content)
            # Keep it executable
            mode = script_path.stat().st_mode
            script_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except OSError:
            return

    def _repair_vibe_install(self, resolved: Path) -> None:
        """Repair stale Mistral Vibe scripts created by pip in our tools dir."""
        try:
            if not resolved.exists():
                return
            resolved_real = resolved.resolve()
            if not str(resolved).startswith(str(self.tools_dir)) and not str(resolved_real).startswith(str(self.tools_dir)):
                return

            bin_dir = resolved.parent
            orig_script = bin_dir / "vibe.orig"

            # Prefer repairing the underlying python script; wrapper stays as-is
            targets: list[Path] = []
            try:
                first_line = resolved.read_text().splitlines()[0]
            except Exception:
                first_line = ""

            if first_line.startswith("#!") and "python" in first_line:
                targets.append(resolved)

            if orig_script.exists():
                targets.append(orig_script)

            for script in targets:
                self._normalize_python_shebang(script)
                self._fix_pip_script_pythonpath(script)
        except OSError:
            return
