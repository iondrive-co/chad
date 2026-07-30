import type { StreamEvent } from "chad-client";
import type { TerminalChunk } from "../hooks/useStream.ts";

/**
 * Builds a Claude-Code-style transcript for the live view panel.
 *
 * The panel is an append-only, terminal-like log of what the agent is doing:
 * individual tool calls (rendered like `● Read(src/foo.py)`) interleaved with
 * the agent's prose, ordered by sequence number.
 *
 * Three things are deliberately filtered out so the panel reads like a real
 * agent harness and never duplicates the chat panel:
 *  - The completion/progress JSON the prompt asks the agent to emit
 *    (e.g. ```json {"change_summary": ...}```). It is machine plumbing, not
 *    something a terminal user should see.
 *  - The parser's collapsed `• 3 files read` summary lines, since we render the
 *    individual tool calls from structured events instead.
 *  - EXPLORATION_RESULT progress lines, which the chat panel already renders
 *    as Discovery bubbles.
 */

export type TranscriptLineKind = "prose" | "tool" | "user";

export interface TranscriptLine {
  kind: TranscriptLineKind;
  text: string;
}

interface Segment {
  seq: number;
  /** Tie-break within the same seq: user prompt (-1) and prose (0) sort before tool calls (1). */
  rank: number;
  kind: "text" | "tool" | "user";
  text: string;
}

function stripAnsi(text: string): string {
  /* eslint-disable no-control-regex */
  return text
    // OSC sequences (window title etc.), terminated by BEL or ESC-backslash
    .replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, "")
    // CSI sequences (colors, cursor movement)
    .replace(/\x1b\[[0-9;]*[a-zA-Z]/g, "")
    // Charset selection (e.g. ESC ( B)
    .replace(/\x1b[()][0-9A-Za-z]/g, "")
    // Any stray escapes left over (e.g. a sequence split across chunks)
    .replace(/\x1b/g, "");
  /* eslint-enable no-control-regex */
}

function truncate(value: string, max: number): string {
  const v = value.trim();
  return v.length > max ? `${v.slice(0, max - 1)}…` : v;
}

/**
 * Gemini/Qwen-style snake_case tool names → canonical names. The server
 * normalizes new events, but events logged by older servers (and any
 * unnormalized CLI stream) must still render canonically.
 */
const TOOL_ALIASES: Record<string, string> = {
  read_file: "Read",
  read_many_files: "Read",
  write_file: "Write",
  replace: "Edit",
  edit: "Edit",
  run_shell_command: "Bash",
  list_directory: "LS",
  glob: "Glob",
  grep: "Grep",
  grep_search: "Grep",
  search_file_content: "Grep",
  web_search: "WebSearch",
  google_web_search: "WebSearch",
  web_fetch: "WebFetch",
  task: "Task",
};

/** Format a tool_call_started event as a Claude-Code-style call line. */
function formatToolLine(data: Record<string, unknown>): string {
  const rawTool = String(data.tool || "tool");
  const tool = TOOL_ALIASES[rawTool] ?? rawTool;
  const args = (data.args as Record<string, unknown>) || {};
  const path = data.path as string | undefined;
  const command = data.command as string | undefined;

  switch (tool) {
    case "Read":
    case "Write":
    case "Edit":
      return `${tool}(${path ?? (args.file_path as string) ?? (args.absolute_path as string) ?? ""})`;
    case "Bash":
      return `Bash(${truncate(String(command ?? args.command ?? ""), 80)})`;
    case "LS":
      return `LS(${path ?? (args.path as string) ?? ""})`;
    case "Glob":
      return `Glob(${(args.pattern as string) ?? path ?? ""})`;
    case "Grep":
      return `Grep(${(args.pattern as string) ?? ""})`;
    case "Task":
      return `Task(${truncate(String(args.description ?? ""), 60)})`;
    case "WebSearch":
      return `WebSearch(${truncate(String(args.query ?? ""), 60)})`;
    case "WebFetch":
      return `WebFetch(${truncate(String(args.url ?? ""), 60)})`;
    default: {
      const keys = Object.keys(args);
      const detail = keys.length ? truncate(JSON.stringify(args), 60) : "";
      return detail ? `${tool}(${detail})` : tool;
    }
  }
}

/**
 * Remove agent-emitted JSON plumbing and parser summary lines from a run of
 * prose. Operates on the assembled prose (not per-chunk) so a JSON block split
 * across streaming chunks is still stripped as a whole.
 */
function cleanProse(text: string): string {
  let t = stripAnsi(text);
  // EXPLORATION_RESULT: progress lines (every provider's prompt asks for them)
  // are rendered as Discovery bubbles in the chat panel — drop the whole line
  // here so the same text is not shown twice.
  t = t.replace(/^[ \t]*EXPLORATION_RESULT:.*$\n?/gm, "");
  // Tool-call markup a model leaked as text (e.g. llama.cpp missing a
  // <function=...> block): machine plumbing, never prose.
  t = t.replace(/<function=[^>]*>[\s\S]*?<\/function>/g, "");
  t = t.replace(/<\/?(?:function|parameter|tool_call)[^>]*>/g, "");
  // Fenced JSON blocks: ```json { ... } ``` or ``` { ... } ``` — only fences
  // whose first non-space char is `{`. Fences can't nest, so lazily matching
  // anything up to the closing fence covers nested {} inside the JSON.
  t = t.replace(/```(?:json)?\s*\{[\s\S]*?\n?```/gi, "");
  // Bare completion JSON: {"change_summary": ...} / completion_status / files_changed
  t = t.replace(/\{[^{}]*"(?:change_summary|completion_status|files_changed)"[^{}]*\}/g, "");
  // Bare progress JSON: {"type": "progress", ...}
  t = t.replace(/\{[^{}]*"type"\s*:\s*"progress"[^{}]*\}/g, "");
  return t;
}

export function buildTranscript(
  chunks: TerminalChunk[],
  events: StreamEvent[],
): TranscriptLine[] {
  const segments: Segment[] = [];

  for (const chunk of chunks) {
    if (chunk.text) {
      segments.push({ seq: chunk.seq ?? 0, rank: 0, kind: "text", text: chunk.text });
    }
  }

  for (const event of events) {
    const data = (event.data as Record<string, unknown>) || {};
    const seq = typeof data.seq === "number" ? data.seq : event.seq ?? 0;
    if (data.type === "tool_call_started") {
      segments.push({ seq, rank: 1, kind: "tool", text: formatToolLine(data) });
    } else if (data.type === "user_message") {
      // What the model was asked — shown so the panel isn't empty while the
      // model works on its first response.
      const content = truncate(String(data.content ?? ""), 300);
      if (content) {
        segments.push({ seq, rank: -1, kind: "user", text: content });
      }
    }
  }

  segments.sort((a, b) => a.seq - b.seq || a.rank - b.rank);

  const lines: TranscriptLine[] = [];
  let buffer = "";

  const flush = () => {
    if (!buffer) return;
    const cleaned = cleanProse(buffer);
    buffer = "";
    let blankRun = 0;
    for (const line of cleaned.split("\n")) {
      if (/^\s*•/.test(line)) continue; // drop collapsed tool summaries
      if (line.trim() === "") {
        blankRun += 1;
        if (blankRun > 1) continue; // collapse blank runs
      } else {
        blankRun = 0;
      }
      lines.push({ kind: "prose", text: line });
    }
  };

  for (const segment of segments) {
    if (segment.kind === "text") {
      buffer += segment.text;
    } else {
      flush();
      lines.push({ kind: segment.kind, text: segment.text });
    }
  }
  flush();

  // Trim leading/trailing blank prose so the panel doesn't open or end on gaps.
  while (lines.length && lines[0].kind === "prose" && lines[0].text.trim() === "") {
    lines.shift();
  }
  while (
    lines.length &&
    lines[lines.length - 1].kind === "prose" &&
    lines[lines.length - 1].text.trim() === ""
  ) {
    lines.pop();
  }

  return lines;
}
