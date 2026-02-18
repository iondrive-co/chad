import { useState, useEffect, useCallback } from "react";
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

export function SessionLog({ api, sessionId }: Props) {
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [logPath, setLogPath] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);

  const loadEvents = useCallback(async () => {
    setLoading(true);
    try {
      const [eventsData, logData] = await Promise.all([
        api.getEvents(sessionId, 0),
        api.getSessionLog(sessionId),
      ]);
      setEvents(eventsData.events as SessionEvent[]);
      setLogPath(logData.log_path);
    } catch {
      // Ignore errors
    } finally {
      setLoading(false);
    }
  }, [api, sessionId]);

  useEffect(() => {
    if (expanded) {
      loadEvents();
    }
  }, [expanded, loadEvents]);

  const handleRefresh = useCallback(() => {
    loadEvents();
  }, [loadEvents]);

  return (
    <div className="session-log">
      <button
        className="session-log-toggle"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? "▼" : "▶"} Session Log
        {logPath && <span className="log-file-name">{getFileName(logPath)}</span>}
      </button>

      {expanded && (
        <div className="session-log-content">
          <div className="session-log-header">
            <button onClick={handleRefresh} disabled={loading}>
              {loading ? "Loading..." : "Refresh"}
            </button>
            {logPath && (
              <span className="log-path" title={logPath}>
                {logPath}
              </span>
            )}
          </div>

          <div className="session-log-events">
            {events.length === 0 ? (
              <div className="no-events">No events recorded</div>
            ) : (
              events.map((event) => (
                <div key={event.seq} className="session-event">
                  <span className="event-seq">#{event.seq}</span>
                  <span className={`event-type event-type-${event.type}`}>
                    {event.type}
                  </span>
                  <span className="event-time">
                    {formatTime(event.ts)}
                  </span>
                  <span className="event-summary">
                    {getEventSummary(event)}
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

function getEventSummary(event: SessionEvent): string {
  switch (event.type) {
    case "session_started":
      return (event.task_description as string) || "Session started";
    case "user_message":
      return truncate((event.content as string) || "", 50);
    case "assistant_message":
      return "Assistant response";
    case "tool_call_started":
      return `Tool: ${event.name || "unknown"}`;
    case "tool_call_finished":
      return `Tool done: ${event.name || "unknown"}`;
    case "milestone":
      return (event.title as string) || (event.summary as string) || "Milestone";
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
