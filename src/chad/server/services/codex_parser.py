"""Normalize Codex `exec` rendered output into clean prose + structured tool calls.

Unlike the stream-json providers (Claude/Qwen/Gemini), Codex (`codex exec`) prints
its own human-rendered transcript to the PTY. Left raw, the live view shows the
launch banner, the echoed prompt, and raw command dumps — nothing like an agent
harness. This parser turns that text stream into the same shape the rest of Chad
uses, so the UI can render a uniform Claude-Code-style transcript for every provider.

Codex output structure::

    [optional ERROR log lines]
    OpenAI Codex vX.Y.Z
    --------
    workdir: ...
    model: ...
    session id: ...
    --------
    user
    <prompt echo>                    <- dropped
    codex
    <agent message>                  <- prose (kept)
    exec
    /bin/bash -lc "<cmd>" in <dir>   <- Bash tool call
     succeeded in Nms:
    <command output>                 <- dropped (results aren't shown, matching Claude)
    codex
    ...

Blocks are introduced by a bare marker line (``user`` / ``codex`` / ``exec`` /
``thinking``), possibly wrapped in ANSI colour codes. Everything before the first
marker is the banner and is dropped.
"""

from __future__ import annotations

import codecs
import re
from typing import Tuple, Union

# A parsed item: ("text", prose) or ("tool", {"tool", "command", "cwd"}).
CodexItem = Union[Tuple[str, str], Tuple[str, dict]]

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# Marker line -> section. "user" is dropped, "exec" becomes a tool call,
# "codex"/"thinking" are agent prose.
_MARKER_SECTIONS = {
    "user": "user",
    "codex": "codex",
    "thinking": "codex",
    "exec": "exec",
}


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def parse_exec_command(line: str) -> Tuple[str, str | None]:
    """Extract the command and working directory from a Codex exec line.

    Examples::

        /bin/bash -lc "find . -maxdepth 2" in /home/x  -> ("find . -maxdepth 2", "/home/x")
        npm test in /home/x                            -> ("npm test", "/home/x")
    """
    line = line.strip()
    cwd: str | None = None
    m = re.search(r"\s+in\s+(/.*\S)\s*$", line)
    if m:
        cwd = m.group(1)
        line = line[: m.start()].rstrip()

    # Unwrap the common `bash -lc "<cmd>"` / `bash -c '<cmd>'` wrapper.
    m2 = re.match(r"^\S*bash\s+-l?c\s+(.*)$", line)
    if m2:
        cmd = m2.group(1).strip()
        if len(cmd) >= 2 and cmd[0] in "\"'" and cmd[-1] == cmd[0]:
            cmd = cmd[1:-1]
    else:
        cmd = line
    return cmd, cwd


class CodexStreamParser:
    """Streaming, line-oriented parser for Codex exec output.

    Feed raw PTY bytes; receive a list of normalized items. Handles markers and
    commands split across arbitrary chunk boundaries by buffering partial lines.
    """

    def __init__(self) -> None:
        # Incremental decoder so multi-byte UTF-8 split across PTY chunks is not corrupted.
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._buffer = ""
        # None = still in the launch banner; otherwise the current section.
        self._section: str | None = None
        self._exec_awaiting_command = False

    def feed(self, data: bytes) -> list[CodexItem]:
        self._buffer += self._decoder.decode(data)
        items: list[CodexItem] = []
        while "\n" in self._buffer:
            idx = self._buffer.index("\n")
            line = self._buffer[:idx]
            self._buffer = self._buffer[idx + 1:]
            self._process_line(line, items)
        return items

    def flush(self) -> list[CodexItem]:
        """Process any trailing partial line at end of a phase."""
        self._buffer += self._decoder.decode(b"", final=True)
        items: list[CodexItem] = []
        if self._buffer:
            line = self._buffer
            self._buffer = ""
            self._process_line(line, items)
        return items

    def _process_line(self, line: str, items: list[CodexItem]) -> None:
        marker = _strip_ansi(line).strip()
        if marker in _MARKER_SECTIONS:
            self._section = _MARKER_SECTIONS[marker]
            self._exec_awaiting_command = self._section == "exec"
            return

        # Codex prints a "tokens used\n<count>" footer at the end of a run; drop it
        # and everything after.
        if marker == "tokens used":
            self._section = "footer"
            return

        # Banner (before any marker), the echoed prompt, and the footer are dropped.
        if self._section in (None, "user", "footer"):
            return

        if self._section == "exec":
            if self._exec_awaiting_command:
                self._exec_awaiting_command = False
                command, cwd = parse_exec_command(_strip_ansi(line))
                if command:
                    items.append(("tool", {"tool": "Bash", "command": command, "cwd": cwd}))
            # Subsequent lines are the command's output / status — dropped, so the
            # transcript stays a clean list of calls + reasoning (as with Claude).
            return

        # Agent prose. Strip ANSI so progress detection and the transcript see
        # plain text (the terminal emulator would strip it for display anyway).
        items.append(("text", _strip_ansi(line) + "\n"))
