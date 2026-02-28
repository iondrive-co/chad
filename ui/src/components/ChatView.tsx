import { useState, useCallback, useRef, useEffect } from "react";
import type { ChadAPI } from "chad-client";
import { useStream } from "../hooks/useStream.ts";
import { TaskForm } from "./TaskForm.tsx";
import { MergePanel } from "./MergePanel.tsx";
import { WorktreeInfo } from "./WorktreeInfo.tsx";
import { SessionLog } from "./SessionLog.tsx";
import { ProjectSettings } from "./ProjectSettings.tsx";

interface Props {
  api: ChadAPI;
  sessionId: string;
  onSessionChange: () => void;
  defaultProjectPath?: string;
  apiBaseUrl?: string;
  token?: string;
  /** Whether the session is active (from polled session list data). */
  sessionActive?: boolean;
}

/** Strip ANSI escape codes for plain-text display. */
function stripAnsi(text: string): string {
  return text.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, "");
}

export function ChatView({
  api,
  sessionId,
  onSessionChange,
  defaultProjectPath = "",
  apiBaseUrl,
  token,
  sessionActive = false,
}: Props) {
  const [taskActive, setTaskActive] = useState(false);
  const [followupText, setFollowupText] = useState("");
  const [sending, setSending] = useState(false);
  const [showMerge, setShowMerge] = useState(false);
  const [lastCodingAgent, setLastCodingAgent] = useState<string | null>(null);
  const outputRef = useRef<HTMLPreElement>(null);

  // Track the event log position at which the current task started, so the
  // stream skips old milestones/events from previous tasks in the same session.
  const streamSinceSeqRef = useRef<number | undefined>(undefined);

  const { terminalOutput, events, completed, error, reset } = useStream(
    taskActive ? sessionId : null,
    streamSinceSeqRef.current,
    apiBaseUrl,
    token,
  );

  // React to session becoming active (from polling or on mount).
  // When another UI starts a task, the polled sessionActive prop flips to true
  // and this effect connects the WebSocket stream.
  useEffect(() => {
    let cancelled = false;
    if (sessionActive && !taskActive) {
      (async () => {
        try {
          const data = await api.getEvents(sessionId, 0, "session_started");
          if (!cancelled) streamSinceSeqRef.current = data.latest_seq;
        } catch {
          // Fall back to streaming all events
        }
        if (!cancelled) setTaskActive(true);
      })();
    } else if (!sessionActive && !taskActive && !terminalOutput) {
      // Not active — check for pending worktree changes to merge
      api.getWorktreeStatus(sessionId).then((status) => {
        if (!cancelled && status.exists && status.has_changes) {
          setShowMerge(true);
        }
      }).catch(() => {
        // Ignore errors checking worktree status
      });
    }
    return () => { cancelled = true; };
  }, [sessionActive]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-scroll terminal output
  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [terminalOutput]);

  // Mark task inactive when stream completes, check for worktree changes
  useEffect(() => {
    if (completed) {
      setTaskActive(false);
      onSessionChange();
      // Check if there are worktree changes to merge
      api.getWorktreeStatus(sessionId).then((status) => {
        if (status.exists && status.has_changes) {
          setShowMerge(true);
        }
      }).catch(() => {
        // Ignore errors checking worktree status
      });
    }
  }, [api, completed, sessionId, onSessionChange]);

  const handleTaskStart = useCallback(async (codingAgent: string) => {
    // Capture the current event log position before the task starts, so the
    // stream only shows events from this task (not old milestones/output).
    try {
      const data = await api.getEvents(sessionId, 0, "session_started");
      streamSinceSeqRef.current = data.latest_seq;
    } catch {
      streamSinceSeqRef.current = undefined;
    }
    reset();
    setTaskActive(true);
    setShowMerge(false);
    setLastCodingAgent(codingAgent);
  }, [api, sessionId, reset]);

  const handleMergeDone = useCallback(() => {
    setShowMerge(false);
    onSessionChange();
  }, [onSessionChange]);

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
      // Resolve the coding agent: prefer lastCodingAgent, fall back to session's coding_account
      let codingAgent = lastCodingAgent;
      if (!codingAgent) {
        const session = await api.getSession(sessionId);
        codingAgent = session.coding_account ?? null;
      }
      if (!codingAgent) {
        setSending(false);
        return;
      }
      const session = await api.getSession(sessionId);
      // Capture event log position before follow-up starts
      try {
        const data = await api.getEvents(sessionId, 0, "session_started");
        streamSinceSeqRef.current = data.latest_seq;
      } catch {
        streamSinceSeqRef.current = undefined;
      }
      await api.startTask(sessionId, {
        project_path: session.project_path || defaultProjectPath,
        task_description: followupText.trim(),
        coding_agent: codingAgent,
        is_followup: true,
      });
      setFollowupText("");
      reset();
      setTaskActive(true);
    } catch {
      // ignore
    } finally {
      setSending(false);
    }
  }, [api, sessionId, followupText, reset, lastCodingAgent, defaultProjectPath]);

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

  // Track current project path for settings
  const [currentProjectPath, setCurrentProjectPath] = useState(defaultProjectPath);
  const [worktreeRefresh, setWorktreeRefresh] = useState(0);

  // Fetch session to get project path
  useEffect(() => {
    api.getSession(sessionId).then((session) => {
      if (session.project_path) {
        setCurrentProjectPath(session.project_path);
      }
    }).catch(() => {
      // Ignore
    });
  }, [api, sessionId]);

  // Refresh worktree info when task completes
  useEffect(() => {
    if (completed) {
      setWorktreeRefresh((v) => v + 1);
    }
  }, [completed]);

  return (
    <div className="chat-view">
      {/* Worktree and session info bar */}
      <div className="session-info-bar">
        <WorktreeInfo
          api={api}
          sessionId={sessionId}
          refreshTrigger={worktreeRefresh}
        />
        <SessionLog api={api} sessionId={sessionId} />
      </div>

      {/* Project settings (collapsible) */}
      {currentProjectPath && (
        <ProjectSettings api={api} projectPath={currentProjectPath} />
      )}

      {/* Task form or streaming output */}
      {!taskActive && !terminalOutput && !showMerge && (
        <TaskForm
          api={api}
          sessionId={sessionId}
          onStart={handleTaskStart}
          defaultProjectPath={defaultProjectPath}
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

      {/* Merge panel - show when task completes with changes */}
      {showMerge && !taskActive && (
        <MergePanel
          api={api}
          sessionId={sessionId}
          onMerged={handleMergeDone}
          onDismiss={handleMergeDone}
        />
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
