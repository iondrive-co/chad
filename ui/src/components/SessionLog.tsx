import { useState, useEffect, useCallback, useRef } from "react";
import type { ChadAPI } from "chad-client";

interface Props {
  api: ChadAPI;
  sessionId: string;
}

interface SessionEvent {
  seq: number;
  type: string;
  ts: string;
  [key: string]: unknown;
}

/** Event types worth showing in the log. Terminal output and bare status are noise. */
const VISIBLE_TYPES = new Set([
  "session_started",
  "session_ended",
  "user_message",
  "assistant_message",
  "tool_call_started",
  "tool_call_finished",
  "milestone",
]);

export function SessionLog({ api, sessionId }: Props) {
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [logPath, setLogPath] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const retryCountRef = useRef(0);

  const loadEvents = useCallback(async () => {
    setLoading(true);
    try {
      const [eventsData, logData] = await Promise.all([
        api.getEvents(sessionId, 0),
        api.getSessionLog(sessionId),
      ]);
      setEvents(eventsData.events as SessionEvent[]);
      setLogPath(logData.log_path);
      return eventsData.events as SessionEvent[];
    } catch {
      // Ignore errors
      return [];
    } finally {
      setLoading(false);
    }
  }, [api, sessionId]);

  // Load log path and events immediately on mount, with retry if no events found
  useEffect(() => {
    let cancelled = false;
    retryCountRef.current = 0;

    const loadWithRetry = async () => {
      const loadedEvents = await loadEvents();
      if (cancelled) return;

      // If no visible events found and we haven't retried too many times, retry after a delay
      const hasVisibleEvents = loadedEvents.some((e) => VISIBLE_TYPES.has(e.type));
      if (!hasVisibleEvents && retryCountRef.current < 3) {
        retryCountRef.current++;
        setTimeout(() => {
          if (!cancelled) loadWithRetry();
        }, 500);
      }
    };

    loadWithRetry();
    return () => { cancelled = true; };
  }, [loadEvents]);

  // Auto-refresh while expanded
  useEffect(() => {
    if (!expanded) return;
    const timer = setInterval(loadEvents, 5000);
    return () => clearInterval(timer);
  }, [expanded, loadEvents]);

  const visibleEvents = events.filter((e) => VISIBLE_TYPES.has(e.type));

  // Build tool_call_id → tool name map for correlating finished events
  const toolCallNames = new Map<string, string>();
  for (const e of events) {
    if (e.type === "tool_call_started" && e.tool_call_id && e.tool) {
      toolCallNames.set(e.tool_call_id as string, e.tool as string);
    }
  }

  return (
    <div className="session-log">
      <button
        className="session-log-toggle"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? "\u25BC" : "\u25B6"} Session Log
        {logPath && <span className="log-file-name">{getFileName(logPath)}</span>}
        {!logPath && <span className="log-file-name">{sessionId.slice(0, 8)}.jsonl</span>}
      </button>

      {expanded && (
        <div className="session-log-content">
          <div className="session-log-header">
            <button onClick={loadEvents} disabled={loading}>
              {loading ? "Loading..." : "Refresh"}
            </button>
            {logPath && (
              <span className="log-path" title={logPath}>
                {logPath}
              </span>
            )}
          </div>

          <div className="session-log-events">
            {visibleEvents.length === 0 ? (
              <div className="no-events">No events recorded yet</div>
            ) : (
              visibleEvents.map((event) => (
                <div key={event.seq} className="session-event">
                  <span className={`event-type event-type-${event.type}`}>
                    {formatEventType(event.type)}
                  </span>
                  <span className="event-time">
                    {formatTime(event.ts)}
                  </span>
                  <span className="event-summary">
                    {getEventSummary(event, toolCallNames)}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function getFileName(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1];
}

function formatTime(ts: string): string {
  try {
    const date = new Date(ts);
    return date.toLocaleTimeString();
  } catch {
    return ts;
  }
}

function formatEventType(type: string): string {
  switch (type) {
    case "session_started": return "started";
    case "session_ended": return "ended";
    case "user_message": return "user";
    case "assistant_message": return "assistant";
    case "tool_call_started": return "tool call";
    case "tool_call_finished": return "tool done";
    case "milestone": return "milestone";
    default: return type;
  }
}

function getEventSummary(event: SessionEvent, toolCallNames: Map<string, string>): string {
  switch (event.type) {
    case "session_started":
      return (event.task_description as string) || "Session started";
    case "user_message":
      return (event.content as string) || "";
    case "assistant_message": {
      const blocks = Array.isArray(event.blocks) ? event.blocks : [];
      const textParts = (blocks as { kind?: string; content?: string }[])
        .filter((b) => b.kind === "text" || b.kind === "thinking")
        .map((b) => (b.content ?? "").trim())
        .filter(Boolean);
      if (textParts.length > 0) {
        return textParts.join(" ");
      }
      return "Assistant response";
    }
    case "tool_call_started": {
      const tool = (event.tool as string) || "unknown";
      // For file ops show path, for bash show command, for MCP show tool_name, otherwise show args
      const detail = (event.path as string) || (event.command as string) || (event.tool_name as string);
      if (detail) {
        return `${tool}(${truncate(detail, 80)})`;
      }
      const args = event.args;
      if (args && typeof args === "object") {
        const argStr = Object.entries(args as Record<string, unknown>)
          .map(([k, v]) => `${k}=${typeof v === "string" ? truncate(v, 30) : String(v)}`)
          .join(", ");
        return `${tool}(${truncate(argStr, 80)})`;
      }
      return `${tool}()`;
    }
    case "tool_call_finished": {
      const tool = toolCallNames.get(event.tool_call_id as string) || (event.tool_call_id as string) || "unknown";
      const isError = event.is_error;
      const summary = (event.llm_summary as string) || "";
      if (isError) return `${tool}: ERROR ${truncate(summary, 60)}`;
      if (summary) return `${tool}: ${truncate(summary, 80)}`;
      return `${tool}: done`;
    }
    case "milestone": {
      const title = (event.title as string) || "";
      const summary = (event.summary as string) || "";
      if (title && summary) return `${title}: ${summary}`;
      return title || summary || "Milestone";
    }
    case "session_ended":
      return (event.reason as string) || "Session ended";
    default:
      return "";
  }
}

function truncate(str: string, len: number): string {
  if (str.length <= len) return str;
  return str.slice(0, len) + "...";
}
