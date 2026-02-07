"""Reusable test utility tools for verifying Chad bugs and features.

These tools wrap common test patterns (API polling, provider simulation,
config parity checking, stream inspection) so that bug reproduction steps
can be expressed as short, readable sequences.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Tool 1: collect_stream_events
# ---------------------------------------------------------------------------

@dataclass
class CollectedEvents:
    """Events collected from a session's EventLog REST endpoint."""

    all_events: list[dict[str, Any]]
    terminal_events: list[dict[str, Any]] = field(default_factory=list)
    structured_events: list[dict[str, Any]] = field(default_factory=list)
    decoded_output: str = ""

    def __post_init__(self) -> None:
        self.terminal_events = [
            e for e in self.all_events if e.get("type") == "terminal_output"
        ]
        self.structured_events = [
            e for e in self.all_events if e.get("type") != "terminal_output"
        ]
        self.decoded_output = "".join(
            e.get("data", "") for e in self.terminal_events
        )


def collect_stream_events(
    client,
    session_id: str,
    timeout: float = 10.0,
    poll_interval: float = 0.3,
    wait_for_completion: bool = True,
) -> CollectedEvents:
    """Poll ``GET /api/v1/sessions/{id}/events`` until the session ends or *timeout* elapses.

    Args:
        client: FastAPI ``TestClient`` instance.
        session_id: Session UUID.
        timeout: Maximum seconds to wait.
        poll_interval: Seconds between polls.
        wait_for_completion: If *True*, keep polling until a ``session_ended``
            event appears or *timeout* is reached.  Otherwise return after the
            first successful poll.

    Returns:
        A :class:`CollectedEvents` with all events, split by type,
        and concatenated decoded terminal output.
    """
    deadline = time.monotonic() + timeout
    since_seq = 0
    all_events: list[dict[str, Any]] = []

    while True:
        resp = client.get(
            f"/api/v1/sessions/{session_id}/events",
            params={"since_seq": since_seq},
        )
        if resp.status_code == 200:
            data = resp.json()
            new_events = data.get("events", [])
            if new_events:
                all_events.extend(new_events)
                since_seq = data.get("latest_seq", since_seq)

            if not wait_for_completion:
                break

            if any(e.get("type") == "session_ended" for e in new_events):
                break

        if time.monotonic() >= deadline:
            break

        time.sleep(poll_interval)

    return CollectedEvents(all_events=all_events)


# ---------------------------------------------------------------------------
# Tool 2: ProviderOutputSimulator
# ---------------------------------------------------------------------------

# Each scenario maps to a Python one-liner that writes canned bytes to stdout.

_QWEN_DUPLICATE_SCRIPT = r'''
import sys, json, time
# Qwen stream-json emits both a "message" event and an "assistant" event
# with the same content, causing duplicates.
msg = {"type": "message", "role": "assistant", "content": "I will fix the bug now."}
sys.stdout.buffer.write((json.dumps(msg) + "\n").encode())
sys.stdout.buffer.flush()
time.sleep(0.05)
asst = {"type": "assistant", "content": [{"type": "text", "text": "I will fix the bug now."}]}
sys.stdout.buffer.write((json.dumps(asst) + "\n").encode())
sys.stdout.buffer.flush()
time.sleep(0.05)
done = {"type": "result", "result": "done"}
sys.stdout.buffer.write((json.dumps(done) + "\n").encode())
sys.stdout.buffer.flush()
'''

_CODEX_SYSTEM_PROMPT_SCRIPT = r'''
import sys, json, time
# Codex emits a system message first, then assistant output.
sys_msg = {"type": "item.completed", "item": {"type": "system_message", "role": "system", "content": [{"type": "text", "text": "You are a coding assistant. Follow instructions carefully."}]}}
sys.stdout.buffer.write((json.dumps(sys_msg) + "\n").encode())
sys.stdout.buffer.flush()
time.sleep(0.05)
asst_msg = {"type": "item.completed", "item": {"type": "agent_message", "role": "assistant", "content": [{"type": "text", "text": "I will start by reading the project files..."}]}}
sys.stdout.buffer.write((json.dumps(asst_msg) + "\n").encode())
sys.stdout.buffer.flush()
'''

_CODEX_TOOL_CALLS_ONLY_SCRIPT = r'''
import sys, json, time
# Codex outputs tool calls with no preceding explanation text.
tc = {"type": "item.completed", "item": {"type": "mcp_tool_call", "name": "read_file", "arguments": "{\"path\": \"/src/main.py\"}", "output": "file contents here"}}
sys.stdout.buffer.write((json.dumps(tc) + "\n").encode())
sys.stdout.buffer.flush()
time.sleep(0.05)
tc2 = {"type": "item.completed", "item": {"type": "function_call", "name": "write_file", "arguments": "{\"path\": \"/src/main.py\", \"content\": \"new\"}"}}
sys.stdout.buffer.write((json.dumps(tc2) + "\n").encode())
sys.stdout.buffer.flush()
'''

_CODEX_GARBLED_BINARY_SCRIPT = r'''
import sys, time
# Codex sometimes outputs large binary/image-like chunks.
garbage = "@@@@@@@%#%%#@@@%#####%@@%%%%#%%%%@@%#%#%%%%%@@@%%%%#%@%%%%#%%%%######%%@@%#%%#%@@#*%@@%%%#%%%%@@##*#@@@%*#####%@@%%%%%%@" * 20
sys.stdout.buffer.write(garbage.encode())
sys.stdout.buffer.flush()
time.sleep(0.05)
sys.stdout.buffer.write(b"\nDone.\n")
sys.stdout.buffer.flush()
'''

SCENARIOS: dict[str, str] = {
    "qwen_duplicate": _QWEN_DUPLICATE_SCRIPT,
    "codex_system_prompt": _CODEX_SYSTEM_PROMPT_SCRIPT,
    "codex_tool_calls_only": _CODEX_TOOL_CALLS_ONLY_SCRIPT,
    "codex_garbled_binary": _CODEX_GARBLED_BINARY_SCRIPT,
}


class ProviderOutputSimulator:
    """Monkeypatch ``build_agent_command`` to emit canned provider output.

    Usage::

        sim = ProviderOutputSimulator(monkeypatch, "qwen_duplicate")
        # Now any task started through the API will run the canned script
        # instead of a real provider CLI.
    """

    def __init__(self, monkeypatch, scenario: str) -> None:
        if scenario not in SCENARIOS:
            raise ValueError(
                f"Unknown scenario {scenario!r}; choose from {sorted(SCENARIOS)}"
            )
        self.scenario = scenario
        script = SCENARIOS[scenario]
        # Monkeypatch build_agent_command to return a python3 -c command
        # that writes the canned output to stdout.
        monkeypatch.setattr(
            "chad.server.services.task_executor.build_agent_command",
            lambda *args, **kwargs: (
                ["python3", "-c", script],
                {},  # env
                None,  # initial_input
            ),
        )


# ---------------------------------------------------------------------------
# Tool 3: TaskPhaseMonitor
# ---------------------------------------------------------------------------

@dataclass
class PhaseEntry:
    """A detected phase within a task's event stream."""

    name: str
    start_seq: int
    terminal_event_count: int = 0
    structured_event_count: int = 0


class TaskPhaseMonitor:
    """Scan collected events for phase transitions.

    Phases are detected from:
    - ``session_started`` → ``"coding"`` phase
    - ``verification_attempt`` → ``"verification_N"`` phase
    - Terminal text containing ``"Phase 1:"``/``"Phase 2:"``/``"continuing"``/``"verification"``

    Usage::

        events = collect_stream_events(client, session_id)
        monitor = TaskPhaseMonitor(events.all_events)
        for phase in monitor.phases:
            print(phase.name, phase.terminal_event_count)
    """

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.phases: list[PhaseEntry] = []
        self._scan()

    def _scan(self) -> None:
        if not self.events:
            self.phases = []
            return

        current_phase = PhaseEntry(name="init", start_seq=0)
        phases: list[PhaseEntry] = [current_phase]

        for event in self.events:
            etype = event.get("type", "")
            seq = event.get("seq", 0)

            # Detect phase transitions from structured events
            if etype == "session_started":
                current_phase = PhaseEntry(name="coding", start_seq=seq)
                phases.append(current_phase)
            elif etype == "verification_attempt":
                attempt = event.get("attempt_number", 1)
                current_phase = PhaseEntry(
                    name=f"verification_{attempt}", start_seq=seq
                )
                phases.append(current_phase)

            # Detect phase markers from terminal output
            if etype == "terminal_output":
                data = event.get("data", "")
                current_phase.terminal_event_count += 1
                if "Phase 1:" in data:
                    current_phase = PhaseEntry(name="phase_1", start_seq=seq)
                    phases.append(current_phase)
                elif "Phase 2:" in data:
                    current_phase = PhaseEntry(name="phase_2", start_seq=seq)
                    phases.append(current_phase)
                elif "continuing" in data.lower():
                    current_phase = PhaseEntry(name="continuation", start_seq=seq)
                    phases.append(current_phase)
            else:
                current_phase.structured_event_count += 1

        # Remove the empty init phase if a real phase was found
        if len(phases) > 1 and phases[0].name == "init":
            phases.pop(0)

        self.phases = phases

    def phase_names(self) -> list[str]:
        """Return just the phase names in order."""
        return [p.name for p in self.phases]

    def terminal_counts_by_phase(self) -> dict[str, int]:
        """Map phase name → terminal event count."""
        return {p.name: p.terminal_event_count for p in self.phases}


# ---------------------------------------------------------------------------
# Tool 4: capture_provider_command
# ---------------------------------------------------------------------------

@dataclass
class CapturedCommand:
    """Result of calling ``build_agent_command`` directly."""

    cmd: list[str]
    env: dict[str, str]
    initial_input: str | None


def capture_provider_command(
    provider: str,
    account_name: str,
    project_path: str | Path,
    task_description: str | None = None,
    screenshots: list[str] | None = None,
    phase: str = "exploration",
    exploration_output: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> CapturedCommand:
    """Call ``build_agent_command()`` directly and return the result.

    This is a pure function call—no monkeypatching needed.
    """
    from chad.server.services.task_executor import build_agent_command

    cmd, env, initial_input = build_agent_command(
        provider=provider,
        account_name=account_name,
        project_path=Path(project_path),
        task_description=task_description,
        screenshots=screenshots,
        phase=phase,
        exploration_output=exploration_output,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    return CapturedCommand(cmd=cmd, env=env, initial_input=initial_input)


# ---------------------------------------------------------------------------
# Tool 5: cli_config_parity_check
# ---------------------------------------------------------------------------

@dataclass
class ConfigParityResult:
    """Result of checking CLI UI config parity."""

    api_keys: set[str]
    cli_keys: set[str]
    missing_from_cli: set[str]


def cli_config_parity_check() -> ConfigParityResult:
    """Check which user-editable config keys are missing from the CLI UI.

    Reads ``CONFIG_BASE_KEYS``, subtracts internal keys, then searches
    ``src/chad/ui/cli/app.py`` for references to each key (using the
    same pattern approach as ``TestConfigUIParity``).

    Returns:
        A :class:`ConfigParityResult` showing what's present and what's missing.
    """
    from chad.util.config_manager import CONFIG_BASE_KEYS

    # Internal keys that are not user-editable
    internal_keys = {
        "password_hash",
        "encryption_salt",
        "accounts",
        "role_assignments",
        "preferences",
        "projects",
        "mock_remaining_usage",
        "mock_context_remaining",
    }

    # Gradio-only keys
    gradio_only = {"ui_mode"}

    api_keys = CONFIG_BASE_KEYS - internal_keys - gradio_only

    # Pattern map matching TestConfigUIParity.KEY_PATTERNS
    key_patterns: dict[str, list[str]] = {
        "verification_agent": ["verification_agent", "verification_pref"],
        "preferred_verification_model": [
            "verification_model",
            "preferred_verification_model",
        ],
        "cleanup_days": [
            "cleanup_days",
            "retention_days",
            "cleanup_settings",
            "retention_input",
        ],
        "provider_fallback_order": ["fallback_order"],
        "usage_switch_threshold": ["usage_threshold", "usage_switch"],
        "context_switch_threshold": ["context_threshold", "context_switch"],
        "max_verification_attempts": [
            "max_verification_attempts",
            "verification_attempts",
        ],
    }

    # Read CLI source
    cli_path = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "chad"
        / "ui"
        / "cli"
        / "app.py"
    )
    content = cli_path.read_text()

    cli_keys: set[str] = set()
    missing: set[str] = set()

    for key in api_keys:
        patterns_to_check = key_patterns.get(key, [key])
        found = False
        for pattern in patterns_to_check:
            search_patterns = [
                rf'"{pattern}"',
                rf"'{pattern}'",
                rf"get_{pattern}",
                rf"set_{pattern}",
                pattern,
            ]
            if any(re.search(p, content, re.IGNORECASE) for p in search_patterns):
                found = True
                break
        if found:
            cli_keys.add(key)
        else:
            missing.add(key)

    return ConfigParityResult(
        api_keys=api_keys, cli_keys=cli_keys, missing_from_cli=missing
    )


# ---------------------------------------------------------------------------
# Tool 6: inspect_stream_output (formerly Tool 7)
# ---------------------------------------------------------------------------

@dataclass
class StreamInspection:
    """Result of inspecting decoded stream output for anomalies."""

    has_raw_json: bool
    json_fragments: list[str]
    has_binary_data: bool
    binary_fragments: list[str]


def inspect_stream_output(decoded_output: str) -> StreamInspection:
    """Scan decoded terminal output for raw JSON and binary-like data.

    Args:
        decoded_output: Concatenated decoded terminal text (from
            ``CollectedEvents.decoded_output``).

    Returns:
        A :class:`StreamInspection` summarising anomalies found.
    """
    # Detect raw JSON patterns that shouldn't appear in user-facing output
    json_patterns = [
        r'\{"type"\s*:',
        r'\{"message"\s*:',
        r'\{"content"\s*:',
        r'\{"item"\s*:',
        r'\{"role"\s*:',
    ]
    json_fragments: list[str] = []
    for pattern in json_patterns:
        for match in re.finditer(pattern, decoded_output):
            # Grab surrounding context (up to 120 chars)
            start = max(0, match.start() - 20)
            end = min(len(decoded_output), match.end() + 100)
            json_fragments.append(decoded_output[start:end])

    # Detect binary-like runs: 10+ consecutive non-alphanumeric, non-space,
    # non-common-punctuation characters from the set @#%*&^
    binary_pattern = r'[@#%*&^]{10,}'
    binary_fragments: list[str] = []
    for match in re.finditer(binary_pattern, decoded_output):
        binary_fragments.append(match.group()[:120])

    return StreamInspection(
        has_raw_json=len(json_fragments) > 0,
        json_fragments=json_fragments,
        has_binary_data=len(binary_fragments) > 0,
        binary_fragments=binary_fragments,
    )
