"""Tests for Codex output normalization into the agent-harness transcript.

Codex prints its own rendered transcript (banner, echoed prompt, `codex`/`exec`
blocks). CodexStreamParser turns that into the same shape the UI renders for every
provider: clean prose plus structured Bash tool calls, with the banner, prompt
echo, and raw command output dropped.

The fixture below is modeled on real `codex exec` output (v0.141.0).
"""

from chad.server.services.codex_parser import CodexStreamParser, parse_exec_command

# Real-shaped Codex exec output, with ANSI on the markers/banner as Codex emits.
CODEX_RAW = (
    "\x1b[2m2026-06-21T10:00:00Z\x1b[0m \x1b[31mERROR\x1b[0m transient startup warning\n"
    "OpenAI Codex v0.141.0\n"
    "--------\n"
    "\x1b[1mworkdir:\x1b[0m /home/miles/chad/.chad-worktrees/00f848b9\n"
    "\x1b[1mmodel:\x1b[0m gpt-5.5\n"
    "\x1b[1mprovider:\x1b[0m openai\n"
    "\x1b[1msandbox:\x1b[0m danger-full-access\n"
    "\x1b[1msession id:\x1b[0m 019ee898-d6d4-7fb0-b043-813354bbb862\n"
    "--------\n"
    "\x1b[36muser\x1b[0m\n"
    "# Project Documentation\n"
    "Read the following project files from disk before making changes:\n"
    "## Verification\n"
    "# Task\n"
    "Summarise this repo\n"
    "Do not run `git commit` or `git add`.\n"
    "\x1b[36mcodex\x1b[0m\n"
    "EXPLORATION_RESULT: The requested project docs are fully read; no code change "
    "is implied by “Summarise this repo,” so I’m gathering structure.\n"
    "\x1b[36mexec\x1b[0m\n"
    "/bin/bash -lc \"find . -maxdepth 2 -type f | sort | sed -n '1,220p'\" "
    "in /home/miles/chad/.chad-worktrees/00f848b9\n"
    " succeeded in 0ms:\n"
    "./.claude/Claude.md\n"
    "./AGENTS.md\n"
    "./pyproject.toml\n"
    "\x1b[36mcodex\x1b[0m\n"
    "EXPLORATION_RESULT: Lint passed with no flake8 output; starting the test suite.\n"
    "\x1b[36mexec\x1b[0m\n"
    "/bin/bash -lc '/home/miles/chad/.venv/bin/python -m flake8 .' in /home/miles/chad\n"
    " succeeded in 793ms:\n"
    "\x1b[36mexec\x1b[0m\n"
    "/bin/bash -lc '/home/miles/chad/.venv/bin/python -m pytest tests/ -v' in /home/miles/chad\n"
    "\x1b[36mcodex\x1b[0m\n"
    "EXPLORATION_RESULT: Pytest collected 1203 tests; no failures so far.\n"
    "tokens used\n"
    "3,988\n"
)


def _run(parser: CodexStreamParser, raw: str, chunk_size: int | None = None):
    """Feed raw text (optionally split into chunks) and return (prose, tool_items)."""
    data = raw.encode()
    items = []
    if chunk_size is None:
        items.extend(parser.feed(data))
    else:
        for i in range(0, len(data), chunk_size):
            items.extend(parser.feed(data[i:i + chunk_size]))
    items.extend(parser.flush())
    prose = "".join(text for kind, text in items if kind == "text")
    tools = [payload for kind, payload in items if kind == "tool"]
    return prose, tools


class TestCodexStreamParser:
    def test_drops_banner_and_header(self):
        prose, _ = _run(CodexStreamParser(), CODEX_RAW)
        assert "OpenAI Codex" not in prose
        assert "workdir:" not in prose
        assert "session id:" not in prose
        assert "danger-full-access" not in prose

    def test_drops_prompt_echo(self):
        prose, _ = _run(CodexStreamParser(), CODEX_RAW)
        assert "# Project Documentation" not in prose
        assert "## Verification" not in prose
        assert "Read the following project files" not in prose
        assert "git commit" not in prose

    def test_keeps_agent_prose(self):
        prose, _ = _run(CodexStreamParser(), CODEX_RAW)
        assert "The requested project docs are fully read" in prose
        assert "starting the test suite" in prose
        assert "Pytest collected 1203 tests" in prose

    def test_exec_becomes_bash_tool_calls(self):
        _, tools = _run(CodexStreamParser(), CODEX_RAW)
        commands = [t["command"] for t in tools]
        assert all(t["tool"] == "Bash" for t in tools)
        assert any("find . -maxdepth 2" in c for c in commands)
        assert any("flake8 ." in c for c in commands)
        assert any("pytest tests/ -v" in c for c in commands)
        # The bash -lc wrapper and the trailing `in <dir>` are stripped from the command.
        assert all("bash -lc" not in c for c in commands)
        assert all(" in /home/miles" not in c for c in commands)

    def test_drops_command_output(self):
        prose, _ = _run(CodexStreamParser(), CODEX_RAW)
        assert "./.claude/Claude.md" not in prose
        assert "./AGENTS.md" not in prose
        assert "succeeded in" not in prose

    def test_drops_tokens_used_footer(self):
        prose, _ = _run(CodexStreamParser(), CODEX_RAW)
        assert "tokens used" not in prose
        assert "3,988" not in prose

    def test_strips_ansi_from_prose(self):
        prose, _ = _run(CodexStreamParser(), CODEX_RAW)
        assert "\x1b[" not in prose

    def test_streaming_matches_whole_feed(self):
        """Splitting input across arbitrary chunk boundaries yields the same result."""
        whole_prose, whole_tools = _run(CodexStreamParser(), CODEX_RAW)
        for size in (1, 3, 7, 50):
            prose, tools = _run(CodexStreamParser(), CODEX_RAW, chunk_size=size)
            assert prose == whole_prose, f"prose differs at chunk_size={size}"
            assert [t["command"] for t in tools] == [t["command"] for t in whole_tools], (
                f"tools differ at chunk_size={size}"
            )

    def test_handles_output_with_no_markers(self):
        """Output before any marker (e.g. a bare banner) is dropped, not leaked."""
        prose, tools = _run(CodexStreamParser(), "just some banner text\nno markers\n")
        assert prose == ""
        assert tools == []


class TestParseExecCommand:
    def test_bash_lc_double_quotes(self):
        cmd, cwd = parse_exec_command('/bin/bash -lc "find . -type f" in /home/x')
        assert cmd == "find . -type f"
        assert cwd == "/home/x"

    def test_bash_lc_single_quotes(self):
        cmd, cwd = parse_exec_command("/bin/bash -lc 'pytest -q' in /home/x/proj")
        assert cmd == "pytest -q"
        assert cwd == "/home/x/proj"

    def test_non_bash_command(self):
        cmd, cwd = parse_exec_command("npm run build in /srv/app")
        assert cmd == "npm run build"
        assert cwd == "/srv/app"

    def test_no_workdir(self):
        cmd, cwd = parse_exec_command('/bin/bash -lc "echo hi"')
        assert cmd == "echo hi"
        assert cwd is None
