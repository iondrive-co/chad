"""Integration tests for critical provider behaviors.

These tests verify behaviors that MUST work for all providers but cannot be
tested with mocks. Run with: CHAD_RUN_PROVIDER_TESTS=1 pytest -v

Each test documents which regression it prevents.
"""

import os
import subprocess
from pathlib import Path

import pytest

# Skip all tests unless explicitly enabled
pytestmark = pytest.mark.skipif(
    os.environ.get("CHAD_RUN_PROVIDER_TESTS") != "1",
    reason="Provider integration tests require CHAD_RUN_PROVIDER_TESTS=1"
)


def get_codex_home() -> Path:
    """Get Codex home directory from config or default."""
    # Try common locations
    for name in ["codex-work", "codex-personal", "default"]:
        path = Path.home() / ".chad" / "codex-homes" / name
        if path.exists():
            return path
    pytest.skip("No Codex home directory found")


def get_claude_config_dir() -> Path:
    """Get Claude config directory from config or default."""
    for name in ["default", "claude-work", "claude-personal"]:
        path = Path.home() / ".chad" / "claude-configs" / name
        if path.exists():
            return path
    pytest.skip("No Claude config directory found")


class TestCodexMultiStepCompletion:
    """Tests that Codex completes multi-step tasks.

    REGRESSION: Codex exec mode was exiting after outputting progress JSON,
    causing agents to stop before doing any actual work. This was because
    the Codex CLI interprets bare JSON as a completion signal.

    FIX: Use markdown format for progress updates instead of JSON.
    See docs/ARCHITECTURE.md "Agent Prompt Formats" section.
    """

    def test_codex_completes_task_with_markdown_progress(self, tmp_path):
        """Verify Codex doesn't exit early when given markdown progress format.

        This test would have FAILED before the markdown progress fix because
        Codex would exit immediately after outputting JSON progress.
        """
        prompt = '''
Create a file called test.py that prints "success".

Use this format for progress updates:
```
**Progress:** What you found
**Location:** Where
**Next:** What's next
```

After creating the file, end with:
```json
{"completion_status": "success", "files_changed": ["test.py"]}
```
'''
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text(prompt)

        codex_home = get_codex_home()
        result = subprocess.run(
            [
                "codex", "exec",
                "--dangerously-bypass-approvals-and-sandbox",
                "--skip-git-repo-check",
                "-C", str(tmp_path),
                "-",
            ],
            stdin=open(prompt_file),
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(codex_home)},
            timeout=180,
        )

        # The file should exist - if Codex exited early, it won't
        test_file = tmp_path / "test.py"
        assert test_file.exists(), (
            f"Codex did not create test.py - likely exited early.\n"
            f"stdout: {result.stdout[-2000:]}\n"
            f"stderr: {result.stderr[-500:]}"
        )

        # Verify content
        content = test_file.read_text()
        assert "success" in content.lower() or "print" in content.lower()

    def test_codex_json_progress_causes_early_exit(self, tmp_path):
        """Demonstrate that JSON progress format causes early exit.

        This test documents the BROKEN behavior - it should fail if Codex
        starts properly handling JSON progress without exiting.

        If this test starts passing, JSON progress format may be safe to use.
        """
        prompt = '''
Create a file called test.py that prints "success".

Output this progress update first:
```json
{"type": "progress", "summary": "Starting task", "location": ".", "next_step": "Creating file"}
```

Then create the file and end with:
```json
{"completion_status": "success", "files_changed": ["test.py"]}
```
'''
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text(prompt)

        codex_home = get_codex_home()
        result = subprocess.run(
            [
                "codex", "exec",
                "--dangerously-bypass-approvals-and-sandbox",
                "--skip-git-repo-check",
                "-C", str(tmp_path),
                "-",
            ],
            stdin=open(prompt_file),
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(codex_home)},
            timeout=180,
        )

        # With JSON progress, the file should NOT exist (Codex exits early)
        test_file = tmp_path / "test.py"

        # This assertion documents the broken behavior
        # If Codex ever fixes this, this test will fail and we can
        # consider re-enabling JSON progress
        if test_file.exists():
            pytest.skip(
                "Codex now handles JSON progress correctly - "
                "JSON format may be safe to use again"
            )

        # Verify we got the progress JSON in output (it was emitted before exit)
        assert '"type": "progress"' in result.stdout or '"type":"progress"' in result.stdout, (
            "Expected progress JSON in output"
        )


class TestClaudeMultiStepCompletion:
    """Tests that Claude Code completes multi-step tasks.

    Claude uses stream-json format and handles progress differently than Codex.
    These tests verify Claude-specific behaviors.
    """

    @pytest.mark.skip(reason="Claude integration test - implement when needed")
    def test_claude_completes_task_with_progress(self, tmp_path):
        """Verify Claude completes tasks with intermediate progress."""
        pass  # TODO: Implement when Claude-specific issues arise


class TestProgressUpdateParsing:
    """Integration tests for progress update parsing with real output.

    These tests use actual provider output to verify parsing works correctly.
    """

    def test_parse_markdown_progress_from_codex_output(self):
        """Verify markdown progress is correctly parsed from real Codex output."""
        from chad.util.prompts import extract_progress_update

        # Real output from Codex with markdown progress
        codex_output = '''
thinking
**Exploring directory structure**
exec
/bin/bash -lc ls in /tmp/test succeeded in 30ms:
file1.txt
file2.py

thinking
**Preparing progress update**
codex
**Progress:** Found 2 files in directory, no existing implementation
**Location:** /tmp/test
**Next:** Creating the requested file

Now creating the file...
'''
        result = extract_progress_update(codex_output)
        assert result is not None, "Failed to parse markdown progress from Codex output"
        assert "Found 2 files" in result.summary
        assert result.location == "/tmp/test"
        assert "Creating" in result.next_step

    def test_parse_json_progress_from_claude_output(self):
        """Verify JSON progress is correctly parsed from real Claude output."""
        from chad.util.prompts import extract_progress_update

        # Real output from Claude with JSON progress (stream-json parsed)
        claude_output = '''
I'll start by exploring the codebase.

```json
{"type": "progress", "summary": "Found authentication module", "location": "src/auth.py:45", "next_step": "Adding validation"}
```

Now implementing the changes...
'''
        result = extract_progress_update(claude_output)
        assert result is not None, "Failed to parse JSON progress from Claude output"
        assert "authentication module" in result.summary
        assert result.location == "src/auth.py:45"


class TestProviderCLIAvailability:
    """Verify provider CLIs are installed and accessible."""

    def test_codex_cli_available(self):
        """Verify codex CLI is installed."""
        result = subprocess.run(
            ["codex", "--version"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Codex CLI not available: {result.stderr}"

    @pytest.mark.skip(reason="Enable when testing Claude")
    def test_claude_cli_available(self):
        """Verify claude CLI is installed."""
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Claude CLI not available: {result.stderr}"
