import { useState, useCallback, useRef, useEffect } from "react";
import type { ChadAPI } from "chad-client";
import { useStream } from "../hooks/useStream.ts";
import { TaskForm } from "./TaskForm.tsx";

interface Props {
  api: ChadAPI;
  serverUrl: string;
  sessionId: string;
  onSessionChange: () => void;
}

/** Strip ANSI escape codes for plain-text display. */
function stripAnsi(text: string): string {
  return text.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, "");
}

export function ChatView({
  api,
  serverUrl,
  sessionId,
  onSessionChange,
}: Props) {
  const [taskActive, setTaskActive] = useState(false);
  const [followupText, setFollowupText] = useState("");
  const [sending, setSending] = useState(false);
  const outputRef = useRef<HTMLPreElement>(null);

  const { terminalOutput, events, completed, error, reset } = useStream(
    taskActive ? serverUrl : "",
    taskActive ? sessionId : null,
  );

  // Auto-scroll terminal output
  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [terminalOutput]);

  // Mark task inactive when stream completes
  useEffect(() => {
    if (completed) {
      setTaskActive(false);
      onSessionChange();
    }
  }, [completed, onSessionChange]);

  const handleTaskStart = useCallback(() => {
    reset();
    setTaskActive(true);
  }, [reset]);

  const handleCancel = useCallback(async () => {
    try {
      await api.cancelSession(sessionId);
    } catch {
      // ignore
    }
  }, [api, sessionId]);

  const handleFollowup = useCallback(async () => {
    if (!followupText.trim()) return;
    setSending(true);
    try {
      await api.sendMessage(sessionId, followupText.trim());
      setFollowupText("");
      reset();
      setTaskActive(true);
    } catch {
      // ignore
    } finally {
      setSending(false);
    }
  }, [api, sessionId, followupText, reset]);

  const handleFollowupKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleFollowup();
      }
    },
    [handleFollowup],
  );

  // Show milestones from structured events
  const milestones = events.filter(
    (e) =>
      e.data.event_type === "milestone" ||
      e.data.type === "milestone",
  );

  return (
    <div className="chat-view">
      {/* Task form or streaming output */}
      {!taskActive && !terminalOutput && (
        <TaskForm
          api={api}
          sessionId={sessionId}
          onStart={handleTaskStart}
        />
      )}

      {/* Terminal output */}
      {(taskActive || terminalOutput) && (
        <div className="terminal-area">
          <div className="terminal-header">
            {taskActive && !completed && (
              <>
                <span className="running-indicator">Running...</span>
                <button className="cancel-btn" onClick={handleCancel}>
                  Cancel
                </button>
              </>
            )}
            {completed && <span className="done-indicator">Completed</span>}
            {error && <span className="error-text">{error}</span>}
          </div>

          {/* Milestones */}
          {milestones.length > 0 && (
            <div className="milestones">
              {milestones.map((m, i) => (
                <div key={i} className="milestone">
                  {String(m.data.summary ?? m.data.text ?? "")}
                </div>
              ))}
            </div>
          )}

          <pre ref={outputRef} className="terminal-output">
            {stripAnsi(terminalOutput)}
          </pre>
        </div>
      )}

      {/* Follow-up input */}
      {terminalOutput && !taskActive && (
        <div className="followup-bar">
          <textarea
            value={followupText}
            onChange={(e) => setFollowupText(e.target.value)}
            onKeyDown={handleFollowupKeyDown}
            placeholder="Send a follow-up message..."
            rows={2}
          />
          <button
            onClick={handleFollowup}
            disabled={sending || !followupText.trim()}
          >
            {sending ? "Sending..." : "Send"}
          </button>
        </div>
      )}
    </div>
  );
}
