import { useState, useCallback, useRef, useEffect } from "react";
import type { ChadAPI, StreamEvent } from "chad-client";
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

  // Historical output/events loaded from persisted log for finished sessions
  const [historicalOutput, setHistoricalOutput] = useState("");
  const [historicalEvents, setHistoricalEvents] = useState<StreamEvent[]>([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);

  // Current task description (extracted from session_started events)
  const [taskDescription, setTaskDescription] = useState<string | null>(null);

  // Track current project path for settings
  const [currentProjectPath, setCurrentProjectPath] = useState(defaultProjectPath);
  const [worktreeRefresh, setWorktreeRefresh] = useState(0);

  // Override coding prompt from ProjectSettings
  const [overrideCodingPrompt, setOverrideCodingPrompt] = useState<string | null>(null);

  // Track the event log position at which the current task started, so the
  // stream skips old milestones/events from previous tasks in the same session.
  const streamSinceSeqRef = useRef<number | undefined>(undefined);

  const { terminalOutput, events, completed, error, reset } = useStream(
    taskActive ? sessionId : null,
    streamSinceSeqRef.current,
    apiBaseUrl,
    token,
  );

  // Combined output: live streaming output or historical output for finished sessions
  const displayOutput = terminalOutput || historicalOutput;
  const displayEvents = events.length > 0 ? events : historicalEvents;

  // Load historical events when session is selected and not active
  // This allows viewing finished sessions from any UI instance
  useEffect(() => {
    let cancelled = false;

    // Reset history when session changes
    setHistoricalOutput("");
    setHistoricalEvents([]);
    setHistoryLoaded(false);
    setTaskDescription(null);

    if (!sessionActive && !taskActive) {
      (async () => {
        try {
          // Fetch all events from the persisted log
          const data = await api.getEvents(sessionId, 0);
          if (cancelled) return;

          // Extract terminal output from terminal_output events
          const terminalEvents = (data.events as { type: string; data?: string }[])
            .filter((e) => e.type === "terminal_output" && e.data);
          if (terminalEvents.length > 0) {
            // Combine all terminal output chunks
            const output = terminalEvents.map((e) => e.data || "").join("");
            setHistoricalOutput(output);
          }

          // Convert events to StreamEvent format for milestones display
          const streamEvents: StreamEvent[] = (data.events as { type: string; seq?: number }[])
            .map((e) => ({
              event_type: "event",
              data: e,
              seq: e.seq ?? null,
            }));
          setHistoricalEvents(streamEvents);
          setHistoryLoaded(true);

          // Extract task description from the most recent session_started event
          const sessionStartedEvents = (data.events as { type: string; task_description?: string }[])
            .filter((e) => e.type === "session_started" && e.task_description);
          if (sessionStartedEvents.length > 0) {
            const latestStart = sessionStartedEvents[sessionStartedEvents.length - 1];
            setTaskDescription(latestStart.task_description ?? null);
          }

          // Check for pending worktree changes to merge
          const status = await api.getWorktreeStatus(sessionId);
          if (!cancelled && status.exists && status.has_changes) {
            setShowMerge(true);
          }
        } catch {
          // Ignore errors loading history
          setHistoryLoaded(true);
        }
      })();
    }

    return () => { cancelled = true; };
  }, [api, sessionId, sessionActive, taskActive]);

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
          // Extract task description from the most recent session_started event
          const sessionStartedEvents = (data.events as { type: string; task_description?: string }[])
            .filter((e) => e.type === "session_started" && e.task_description);
          if (!cancelled && sessionStartedEvents.length > 0) {
            const latestStart = sessionStartedEvents[sessionStartedEvents.length - 1];
            setTaskDescription(latestStart.task_description ?? null);
          }
        } catch {
          // Fall back to streaming all events
        }
        if (!cancelled) setTaskActive(true);
      })();
    }
    return () => { cancelled = true; };
  }, [sessionActive]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-scroll terminal output (live or historical)
  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [terminalOutput, historicalOutput]);

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

  const handleTaskStart = useCallback(async (codingAgent: string, taskDesc: string) => {
    // Capture the current event log position before the task starts, so the
    // stream only shows events from this task (not old milestones/output).
    try {
      const data = await api.getEvents(sessionId, 0, "session_started");
      streamSinceSeqRef.current = data.latest_seq;
    } catch {
      streamSinceSeqRef.current = undefined;
    }
    reset();
    // Clear historical output when starting a new task
    setHistoricalOutput("");
    setHistoricalEvents([]);
    setTaskActive(true);
    setShowMerge(false);
    setLastCodingAgent(codingAgent);
    setTaskDescription(taskDesc);
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
      const followupDesc = followupText.trim();
      await api.startTask(sessionId, {
        project_path: session.project_path || currentProjectPath || defaultProjectPath,
        task_description: followupDesc,
        coding_agent: codingAgent,
        is_followup: true,
      });
      setFollowupText("");
      reset();
      setTaskActive(true);
      setTaskDescription(followupDesc);
    } catch {
      // ignore
    } finally {
      setSending(false);
    }
  }, [api, sessionId, followupText, reset, lastCodingAgent, defaultProjectPath, currentProjectPath]);

  const handleFollowupKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleFollowup();
      }
    },
    [handleFollowup],
  );

  // Show milestones from structured events (live or historical)
  const milestones = displayEvents.filter(
    (e) =>
      e.data.event_type === "milestone" ||
      e.data.type === "milestone",
  );

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

  const handleProjectPathChange = useCallback((path: string) => {
    setCurrentProjectPath(path);
  }, []);

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

      {/* Task description - shown when a task is running or has output */}
      {taskDescription && (taskActive || displayOutput) && (
        <div className="task-description-bar">
          <span className="task-description-label">Task:</span>
          <span className="task-description-text">{taskDescription}</span>
        </div>
      )}

      {/* Project settings (collapsible) - always shown */}
      <ProjectSettings
        api={api}
        projectPath={currentProjectPath || defaultProjectPath}
        onProjectPathChange={handleProjectPathChange}
        onPromptsChange={setOverrideCodingPrompt}
      />

      {/* Task form or streaming output */}
      {!taskActive && !displayOutput && !showMerge && historyLoaded && (
        <TaskForm
          api={api}
          sessionId={sessionId}
          onStart={handleTaskStart}
          projectPath={currentProjectPath || defaultProjectPath}
          overridePrompt={overrideCodingPrompt}
        />
      )}

      {/* Terminal output (live or historical) */}
      {(taskActive || displayOutput) && (
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
            {/* Show completed for live completion or historical sessions with output */}
            {(completed || (historicalOutput && !taskActive)) && (
              <span className="done-indicator">Completed</span>
            )}
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
            {stripAnsi(displayOutput)}
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
      {displayOutput && !taskActive && (
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
