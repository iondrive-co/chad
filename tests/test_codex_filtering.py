"""Tests for Codex prompt echo filtering."""

import re


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    ansi_pattern = re.compile(r'\x1b\[[0-9;]*m')
    return ansi_pattern.sub('', text)


# Simulated Codex output with ANSI codes
CODEX_RAW_OUTPUT = """\
\x1b[2m2026-02-05T21:42:38.165352Z\x1b[0m \x1b[31mERROR\x1b[0m some error message
OpenAI Codex v0.92.0 (research preview)
--------
\x1b[1mworkdir:\x1b[0m /tmp/tmpjwlejti6
\x1b[1mmodel:\x1b[0m gpt-5.2-codex
\x1b[1mprovider:\x1b[0m openai
\x1b[1mapproval:\x1b[0m never
\x1b[1msandbox:\x1b[0m danger-full-access
\x1b[1msession id:\x1b[0m 019c2fc1-d7a7-7d13-ac6a-0229bdab82ec
--------
\x1b[36muser\x1b[0m

## Verification

This is the prompt that should be filtered out.
It contains the user's task description.

\x1b[36mmcp startup:\x1b[0m no servers
\x1b[1mthinking\x1b[0m
**Agent is now working on the task**
This content should be shown to the user.
"""


def filter_codex_output(raw_output: str) -> str:
    """Filter Codex prompt echo from output.

    Returns the content that should be shown to the user.
    """
    normalized = raw_output.replace("\r\n", "\n").replace("\r", "\n")

    # Strip ANSI codes for pattern matching
    ansi_pattern = re.compile(r'\x1b\[[0-9;]*m')
    stripped = ansi_pattern.sub('', normalized)

    # Find the "user" marker (after second --------)
    user_line_match = re.search(r'\n--------\nuser\n', stripped)

    if not user_line_match:
        # No prompt echo detected, return as-is
        return raw_output

    # Find the position in original string
    user_pattern = re.compile(r'\n--------\n(?:\x1b\[[0-9;]*m)*user(?:\x1b\[[0-9;]*m)*\n')
    match = user_pattern.search(normalized)

    if not match:
        return raw_output

    # Content before the "user" line (banner + header)
    pre_echo = normalized[:match.start()]

    # Content after the "user" line (need to find mcp startup)
    post_user = normalized[match.end():]
    stripped_post = ansi_pattern.sub('', post_user)

    if "mcp startup:" not in stripped_post.lower():
        # No end marker yet, can't filter
        return raw_output

    # Find mcp startup marker
    mcp_pattern = re.compile(r'(?:\x1b\[[0-9;]*m)*mcp startup:(?:\x1b\[[0-9;]*m)*[^\n]*\n', re.IGNORECASE)
    mcp_match = mcp_pattern.search(post_user)

    if mcp_match:
        agent_output = post_user[mcp_match.end():]
    else:
        # Fallback
        marker_pos = stripped_post.lower().find("mcp startup:")
        newline_after = stripped_post.find("\n", marker_pos)
        if newline_after != -1:
            agent_output = post_user[newline_after + 1:]
        else:
            agent_output = ""

    # Return pre-echo (header) + agent output
    return pre_echo + "\n" + agent_output


class TestCodexFiltering:
    """Tests for Codex output filtering."""

    def test_filter_removes_prompt_echo(self):
        """Filter should remove the echoed prompt."""
        result = filter_codex_output(CODEX_RAW_OUTPUT)

        # Should NOT contain the prompt text
        assert "This is the prompt that should be filtered out" not in result
        assert "It contains the user's task description" not in result
        assert "## Verification" not in result

    def test_filter_keeps_banner_and_header(self):
        """Filter should keep the Codex banner and session info."""
        result = filter_codex_output(CODEX_RAW_OUTPUT)

        # Should contain banner
        assert "OpenAI Codex v0.92.0" in result

        # Should contain header info
        stripped = strip_ansi(result)
        assert "workdir:" in stripped
        assert "model:" in stripped
        assert "session id:" in stripped

    def test_filter_keeps_agent_output(self):
        """Filter should keep the agent's actual work output."""
        result = filter_codex_output(CODEX_RAW_OUTPUT)

        # Should contain agent work
        assert "Agent is now working on the task" in result
        assert "This content should be shown to the user" in result

    def test_filter_handles_no_prompt_echo(self):
        """Filter should pass through output without prompt echo."""
        simple_output = "Just some output\nNo markers here\n"
        result = filter_codex_output(simple_output)
        assert result == simple_output

    def test_filter_handles_ansi_codes_in_markers(self):
        """Filter should handle ANSI codes in the user/mcp markers."""
        # The actual output has ANSI codes like [36muser[0m
        result = filter_codex_output(CODEX_RAW_OUTPUT)

        # Verify we got to the agent output
        assert "thinking" in strip_ansi(result) or "Agent is now working" in result


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
