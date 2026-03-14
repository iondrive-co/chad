"""Lightweight agent runner for project autoconfiguration.

Runs a coding agent as a simple subprocess to discover project settings.
No PTY, no worktree, no continuation logic — just a one-shot query.
"""

import json
import logging
import os
import re
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from chad.util.installer import AIToolInstaller

logger = logging.getLogger(__name__)

_installer = AIToolInstaller()

AUTOCONFIGURE_PROMPT = (
    "IMPORTANT: Be fast. Read only the minimum files needed, then output the JSON.\n"
    "1. Read pyproject.toml or package.json (whichever exists at the root)\n"
    "2. Run: ls to see top-level files\n"
    "3. If there is a ui/ or frontend/ or web/ subdirectory, read its package.json "
    "or vite.config.* for the dev server port and start command\n"
    "4. Output a JSON object with exactly these keys:\n"
    "lint_command - shell command to lint (null if none)\n"
    "test_command - shell command to run tests (null if none)\n"
    "preview_port - dev server port number (null if none)\n"
    "preview_command - shell command to start the dev server from the project root, "
    "e.g. 'npm run dev', 'cd ui && npm run dev', 'python manage.py runserver' "
    "(null if none)\n"
    "instructions_paths - list of existing doc files like AGENTS.md, CLAUDE.md, "
    "CONTRIBUTING.md (empty list if none)\n"
    "Output ONLY the JSON object, nothing else. Do not read more files than necessary."
)


def _resolve_tool(binary: str) -> str:
    resolved = _installer.resolve_tool_path(binary)
    return str(resolved) if resolved else binary


def _build_env() -> dict[str, str]:
    """Build env dict with Chad tool paths."""
    env = dict(os.environ)
    extra_paths = [
        _installer.bin_dir,
        _installer.tools_dir / "node_modules" / ".bin",
    ]
    prepend = [str(p) for p in extra_paths if p.exists()]
    if prepend:
        env["PATH"] = os.pathsep.join(prepend + [env.get("PATH", "")])
    return env


def _build_command(
    provider: str,
    account_name: str,
    project_path: Path,
    prompt: str,
) -> tuple[list[str], dict[str, str], str | None]:
    """Build a minimal CLI command for autoconfigure — no worktree, no scaffolding."""
    env = _build_env()
    stdin_input: str | None = None

    if provider == "anthropic":
        config_dir = Path.home() / ".chad" / "claude-configs" / account_name
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        cmd = [
            _resolve_tool("claude"),
            "-p",
            "--output-format", "text",
            "--max-turns", "3",
            prompt,
        ]
    elif provider == "openai":
        codex_home = Path.home() / ".chad" / "codex-homes" / account_name
        env["HOME"] = str(codex_home)
        cmd = [
            _resolve_tool("codex"),
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "-C", str(project_path),
            "-",
        ]
        stdin_input = prompt + "\n"
    elif provider == "gemini":
        cmd = [_resolve_tool("gemini"), "-y", "-p", prompt]
    elif provider == "qwen":
        cmd = [_resolve_tool("qwen"), "-y", "-p", prompt]
    elif provider == "mistral":
        cmd = [_resolve_tool("vibe"), "--output", "text", "-p", prompt]
    elif provider == "opencode":
        cmd = [_resolve_tool("opencode"), "run", "--format", "json", prompt]
    elif provider == "kimi":
        cmd = [_resolve_tool("kimi"), "--print", "-p", prompt]
    elif provider == "mock":
        # Mock provider — return a canned JSON response
        cmd = ["echo", json.dumps({
            "lint_command": None,
            "test_command": None,
            "preview_port": None,
            "preview_command": None,
            "instructions_paths": [],
        })]
    else:
        raise ValueError(f"Unsupported provider: {provider}")

    return cmd, env, stdin_input


def _extract_json(text: str) -> dict | None:
    """Extract a JSON settings object from agent output.

    Uses the LAST match to skip prompt echoes from providers like Codex
    that repeat the input before responding.
    """
    # Try code blocks first (last one wins)
    matches = list(re.finditer(r'```(?:json)?\s*(\{.+?\})\s*```', text, re.DOTALL))
    for match in reversed(matches):
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            continue

    # Try to find a JSON object containing our expected keys (last match wins)
    for pattern in [
        r'\{[^{}]*"lint_command"[^{}]*\}',
        r'\{[^{}]*"test_command"[^{}]*\}',
    ]:
        all_matches = list(re.finditer(pattern, text, re.DOTALL))
        for match in reversed(all_matches):
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                continue

    return None


def _write_debug_log(job_id: str, output: str, project_path: Path) -> None:
    """Write autoconfigure output to a debug log file."""
    log_dir = Path.home() / ".chad" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"autoconfigure-{job_id}.log"
    try:
        with open(log_file, "w") as f:
            f.write(f"timestamp: {datetime.now().isoformat()}\n")
            f.write(f"project: {project_path}\n")
            f.write(f"---\n{output}\n")
        logger.info("Autoconfigure log written to %s", log_file)
    except OSError:
        pass


class AutoconfigureJob:
    """A running or completed autoconfigure job."""

    def __init__(self) -> None:
        self.status: str = "running"
        self.result: dict | None = None
        self.error: str | None = None
        self.output_lines: list[str] = []
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None

    def cancel(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        self.status = "failed"
        self.error = "Cancelled"


# Track active jobs by an opaque job ID
_jobs: dict[str, AutoconfigureJob] = {}
_job_counter = 0
_lock = threading.Lock()


def start_autoconfigure(
    provider: str,
    account_name: str,
    project_path: Path,
    timeout: int = 120,
) -> str:
    """Start an autoconfigure job in a background thread.

    Returns a job ID for polling.
    """
    global _job_counter
    with _lock:
        _job_counter += 1
        job_id = f"autoconf-{_job_counter}"

    job = AutoconfigureJob()
    _jobs[job_id] = job

    def _run():
        try:
            cmd, env, stdin_input = _build_command(
                provider, account_name, project_path, AUTOCONFIGURE_PROMPT,
            )

            proc = subprocess.Popen(
                cmd,
                cwd=str(project_path),
                stdin=subprocess.PIPE if stdin_input else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
            )
            job._proc = proc

            # Send stdin if needed, then close
            if stdin_input and proc.stdin:
                proc.stdin.write(stdin_input.encode())
                proc.stdin.close()

            # Read output line-by-line for live streaming
            collected: list[str] = []
            deadline = threading.Event()
            timer = threading.Timer(timeout, lambda: deadline.set())
            timer.daemon = True
            timer.start()

            for raw_line in proc.stdout:
                if deadline.is_set():
                    proc.kill()
                    proc.wait()
                    timer.cancel()
                    # Try to extract results from partial output before giving up
                    output = "\n".join(collected)
                    _write_debug_log(job_id, output, project_path)
                    discovered = _extract_json(output)
                    if discovered:
                        job.result = discovered
                        job.status = "completed"
                    else:
                        job.status = "failed"
                        job.error = "Timed out"
                    return

                line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                collected.append(line)
                job.output_lines.append(line)

            timer.cancel()
            proc.wait()

            if job.status == "failed":
                # Was cancelled
                return

            output = "\n".join(collected)
            _write_debug_log(job_id, output, project_path)

            discovered = _extract_json(output)

            if discovered:
                job.result = discovered
                job.status = "completed"
            else:
                job.status = "failed"
                snippet = output[:200]
                job.error = f"Could not parse JSON from agent output: {snippet}"

        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)

    thread = threading.Thread(target=_run, daemon=True)
    job._thread = thread
    thread.start()

    return job_id


def get_job(job_id: str) -> AutoconfigureJob | None:
    return _jobs.get(job_id)


def cleanup_job(job_id: str) -> None:
    _jobs.pop(job_id, None)
