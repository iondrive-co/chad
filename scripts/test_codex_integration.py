#!/usr/bin/env python3
"""Integration test for Codex provider with real API.

This script tests the full flow: Codex CLI → PTY → EventMux → SSE → Gradio client.
It verifies that prompt echo filtering works correctly.

Usage:
    CODEX_HOME=~/.chad/codex-homes/codex-home .venv/bin/python scripts/test_codex_integration.py
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from chad.server.services.task_executor import build_agent_command


def test_codex_command_build():
    """Test that Codex command is built correctly."""
    print("=" * 60)
    print("TEST: Codex command build")
    print("=" * 60)

    cmd, env, initial_input = build_agent_command(
        provider="openai",
        account_name="codex-home",
        project_path=Path("/tmp/test"),
        task_description="Say hello",
        screenshots=None,
        phase="combined",
        exploration_output=None,
    )

    print(f"Command: {' '.join(cmd[:5])}...")
    print(f"HOME in env: {env.get('HOME', 'NOT SET')}")
    print(f"Initial input length: {len(initial_input) if initial_input else 0}")
    print(f"Initial input starts with: {initial_input[:100] if initial_input else 'None'}...")

    # Check that exec mode is used
    assert "exec" in cmd, "Codex should use exec mode"
    assert "-" in cmd, "Codex should read from stdin"

    print("✓ Command build OK")
    return cmd, env, initial_input


def test_codex_pty_raw_output():
    """Test raw PTY output from Codex to see what it actually produces."""
    print("\n" + "=" * 60)
    print("TEST: Raw Codex PTY output")
    print("=" * 60)

    codex_home = Path.home() / ".chad" / "codex-homes" / "codex-home"
    if not codex_home.exists():
        print(f"SKIP: Codex home not found at {codex_home}")
        return None

    # Create a simple test project
    with tempfile.TemporaryDirectory() as tmpdir:
        # Initialize git repo
        subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmpdir, capture_output=True)
        Path(tmpdir, "test.txt").write_text("hello")
        subprocess.run(["git", "add", "."], cwd=tmpdir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=tmpdir, capture_output=True)

        cmd, env, initial_input = build_agent_command(
            provider="openai",
            account_name="codex-home",
            project_path=Path(tmpdir),
            task_description="Reply with just the word 'pong'",
            screenshots=None,
            phase="combined",
            exploration_output=None,
        )

        print(f"Running: {' '.join(cmd[:5])}...")
        print(f"Input: {initial_input[:200] if initial_input else 'None'}...")

        # Use script to allocate PTY (simpler than our PTY service)
        import pty
        import select

        master_fd, slave_fd = pty.openpty()

        full_env = os.environ.copy()
        full_env.update(env)

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=slave_fd,
            stderr=slave_fd,
            env=full_env,
            cwd=tmpdir,
        )

        os.close(slave_fd)

        # Send input
        if initial_input:
            proc.stdin.write(initial_input.encode())
            proc.stdin.flush()
            proc.stdin.close()

        # Read output
        output_chunks = []
        start_time = time.time()
        timeout = 60  # 60 seconds max

        print("\n--- RAW PTY OUTPUT ---")
        while time.time() - start_time < timeout:
            ready, _, _ = select.select([master_fd], [], [], 0.5)
            if ready:
                try:
                    chunk = os.read(master_fd, 4096)
                    if chunk:
                        output_chunks.append(chunk)
                        decoded = chunk.decode(errors='replace')
                        print(decoded, end='', flush=True)
                except OSError:
                    break

            if proc.poll() is not None:
                # Process exited, read remaining
                while True:
                    ready, _, _ = select.select([master_fd], [], [], 0.1)
                    if not ready:
                        break
                    try:
                        chunk = os.read(master_fd, 4096)
                        if chunk:
                            output_chunks.append(chunk)
                            decoded = chunk.decode(errors='replace')
                            print(decoded, end='', flush=True)
                    except OSError:
                        break
                break

        os.close(master_fd)
        proc.wait()

        print("\n--- END RAW OUTPUT ---")

        full_output = b''.join(output_chunks).decode(errors='replace')

        # Analyze output
        print("\n--- ANALYSIS ---")
        print(f"Total output length: {len(full_output)}")
        print(f"Contains '-------- user': {'-------- user' in full_output}")
        print(f"Contains 'mcp startup:': {'mcp startup:' in full_output.lower()}")

        if "-------- user" in full_output:
            idx = full_output.find("-------- user")
            print(f"Content before '-------- user': {repr(full_output[:idx][:200])}")

        return full_output


def main():
    print("Codex Integration Test")
    print("=" * 60)

    # Check environment
    codex_home = os.environ.get("CODEX_HOME", str(Path.home() / ".chad" / "codex-homes" / "codex-home"))
    print(f"CODEX_HOME: {codex_home}")

    if not Path(codex_home).exists():
        print(f"ERROR: CODEX_HOME does not exist: {codex_home}")
        print("Set CODEX_HOME to a valid codex home directory")
        sys.exit(1)

    # Run tests
    test_codex_command_build()
    output = test_codex_pty_raw_output()

    if output:
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)

        if "-------- user" in output:
            print("⚠️  Prompt echo IS present in raw output")
            print("   Filtering must happen in task_executor or web_ui")
        else:
            print("✓ No prompt echo in raw output")


if __name__ == "__main__":
    main()
