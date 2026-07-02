"""Integration tests for the local provider against a real OpenAI-compatible server.

These run the actual Qwen Code CLI against the model server configured via
ConfigManager.get_local_endpoint() (default http://localhost:8000, e.g. a
llama.cpp or vLLM instance). They verify the full chain that unit tests mock:
build_agent_command → qwen CLI → OpenAI-compatible API → tool calls → file edits.

Run with: CHAD_RUN_PROVIDER_TESTS=1 pytest tests/provider_integration/ -k local -v
"""

import json
import os
import subprocess

import pytest

# Skip all tests unless explicitly enabled
pytestmark = pytest.mark.skipif(
    os.environ.get("CHAD_RUN_PROVIDER_TESTS") != "1",
    reason="Provider integration tests require CHAD_RUN_PROVIDER_TESTS=1",
)


def _local_endpoint_or_skip() -> str:
    """Return the configured local endpoint, skipping if no server is reachable."""
    from chad.util.config_manager import ConfigManager
    from chad.util.providers import discover_local_models

    endpoint = ConfigManager().get_local_endpoint()
    try:
        models = discover_local_models(endpoint)
    except (OSError, ValueError):
        pytest.skip(f"No local model server reachable at {endpoint}")
    if not models:
        pytest.skip(f"Local model server at {endpoint} reports no models")
    return endpoint


class TestLocalProviderIntegration:
    """End-to-end tests against the real local model server."""

    def test_endpoint_reports_models(self):
        """The configured endpoint serves an OpenAI-compatible /v1/models list."""
        from chad.util.config_manager import ConfigManager
        from chad.util.providers import build_local_env, discover_local_models

        endpoint = _local_endpoint_or_skip()
        models = discover_local_models(endpoint)

        # build_local_env must resolve the served model for unpinned accounts
        env = build_local_env(endpoint, None)
        assert env["OPENAI_MODEL"] == models[0]
        assert env["OPENAI_BASE_URL"] == endpoint.rstrip("/") + "/v1"
        assert ConfigManager().get_local_endpoint() == endpoint

    def test_local_provider_creates_file(self, tmp_path):
        """The full agent command performs a real file edit through the local model.

        This is the critical path: if the qwen CLI can't authenticate against the
        endpoint, doesn't receive parsed tool calls, or the model can't drive the
        CLI's tools, no file appears and the local provider is broken.
        """
        from chad.server.services.task_executor import build_agent_command

        _local_endpoint_or_skip()

        cmd, env, initial_input = build_agent_command(
            "local",
            "local-test",
            tmp_path,
            override_prompt=(
                "Create a file named marker.txt in the current directory "
                "containing exactly the word DONE. Do nothing else."
            ),
        )
        assert initial_input is None

        result = subprocess.run(
            cmd,
            cwd=tmp_path,
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=600,
        )

        marker = tmp_path / "marker.txt"
        assert marker.exists(), (
            f"Local provider did not create marker.txt.\n"
            f"exit: {result.returncode}\n"
            f"stdout: {result.stdout[-3000:]}\n"
            f"stderr: {result.stderr[-1000:]}"
        )
        assert "DONE" in marker.read_text()

        # stream-json output must be parseable (task_executor relies on it)
        json_lines = [
            line for line in result.stdout.splitlines() if line.strip().startswith("{")
        ]
        assert json_lines, f"No stream-json output found:\n{result.stdout[-2000:]}"
        events = [json.loads(line) for line in json_lines]
        assert any(e.get("type") == "result" for e in events)
