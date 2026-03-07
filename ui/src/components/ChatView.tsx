import { useState, useCallback, useRef, useEffect, DragEvent } from "react";
import type { ChadAPI, ConversationItem, Account } from "chad-client";
import { useStream } from "../hooks/useStream.ts";
import { MergePanel } from "./MergePanel.tsx";
import { WorktreeInfo } from "./WorktreeInfo.tsx";
import { SessionLog } from "./SessionLog.tsx";
import { ProjectSettings } from "./ProjectSettings.tsx";
import { AccountPicker } from "./AccountPicker.tsx";

interface UploadedScreenshot {
  path: string;
  filename: string;
  previewUrl: string;
}

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

function normalizeLineEndings(text: string): string {
  return text.replace(/\r\n?/g, "\n");
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
  const [sending, setSending] = useState(false);
  const [showMerge, setShowMerge] = useState(false);
  const [codingAccount, setCodingAccount] = useState<Account | null>(null);
  const [conversation, setConversation] = useState<ConversationItem[]>([]);
  const [conversationError, setConversationError] = useState<string | null>(null);
  const conversationSeqRef = useRef(0);
  const [inputText, setInputText] = useState("");
  const [hasRunTask, setHasRunTask] = useState(false);
  const [wasCancelled, setWasCancelled] = useState(false);
  const outputRef = useRef<HTMLPreElement>(null);
  const convoRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Screenshot attachments for task creation
  const [screenshots, setScreenshots] = useState<UploadedScreenshot[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  // Historical output/events loaded from persisted log for finished sessions
  const [historicalOutput, setHistoricalOutput] = useState("");

  // Current task description and verification agent (extracted from session_started events)
  const [taskDescription, setTaskDescription] = useState<string | null>(null);
  const [verificationAgent, setVerificationAgent] = useState<string | null>(null);

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
  const displayOutput = normalizeLineEndings(terminalOutput || historicalOutput);

  const mapEventToConversationItem = useCallback(
    (data: any, seq: number | null): ConversationItem | null => {
      const type = data.type || data.event_type;
      if (!type) return null;

      if (type === "user_message") {
        return {
          seq: seq ?? 0,
          ts: data.ts ?? "",
          type: "user",
          content: String(data.content ?? ""),
        };
      }

      if (type === "assistant_message") {
        const blocks = Array.isArray(data.blocks) ? data.blocks : [];
        const textParts = blocks
          .filter((b: any) => ["text", "thinking", "error"].includes(b.kind))
          .map((b: any) => String(b.content ?? "").trim())
          .filter(Boolean);
        return {
          seq: seq ?? 0,
          ts: data.ts ?? "",
          type: "assistant",
          content: textParts.join("\n"),
          blocks,
        };
      }

      if (type === "milestone") {
        return {
          seq: seq ?? 0,
          ts: data.ts ?? "",
          type: "milestone",
          milestone_type: data.milestone_type ?? "",
          title: data.title ?? "",
          summary: data.summary ?? "",
        };
      }

      return null;
    },
    [],
  );

  // Load historical output when session is selected and not active
  useEffect(() => {
    let cancelled = false;
    setHistoricalOutput("");
    setTaskDescription(null);
    setVerificationAgent(null);

    if (!sessionActive && !taskActive) {
      (async () => {
        try {
          const data = await api.getEvents(sessionId, 0, "terminal_output,session_started,session_ended");
          if (cancelled) return;

          const terminalEvents = (data.events as { type: string; data?: string }[])
            .filter((e) => e.type === "terminal_output" && e.data);
          if (terminalEvents.length > 0) {
            const output = terminalEvents.map((e) => e.data || "").join("");
            setHistoricalOutput(normalizeLineEndings(output));
          }

          const starts = (data.events as { type: string; task_description?: string; verification_account?: string }[])
            .filter((e) => e.type === "session_started" && e.task_description);
          if (starts.length > 0) {
            const latestStart = starts[starts.length - 1];
            setTaskDescription(latestStart.task_description ?? null);
            setVerificationAgent(latestStart.verification_account ?? null);
            setHasRunTask(true);
          } else {
            setHasRunTask(false);
          }

          // Check if the last session_ended was a cancellation
          const ends = (data.events as { type: string; reason?: string }[])
            .filter((e) => e.type === "session_ended");
          if (ends.length > 0) {
            const lastEnd = ends[ends.length - 1];
            setWasCancelled(lastEnd.reason === "cancelled");
          }

          const status = await api.getWorktreeStatus(sessionId);
          if (!cancelled && status.exists && status.has_changes) {
            setShowMerge(true);
          }
        } catch {
          /* ignore */
        }
      })();
    }

    return () => { cancelled = true; };
  }, [api, sessionId, sessionActive, taskActive]);

  // Load latest conversation for this session (latest task only)
  useEffect(() => {
    let cancelled = false;
    setConversation([]);
    conversationSeqRef.current = 0;

    (async () => {
      try {
        const convo = await api.getConversation(sessionId, 0);
        if (cancelled) return;
        setConversation(convo.items);
        setTaskDescription(convo.task.task_description || null);
        setVerificationAgent((convo.task as { verification_account?: string }).verification_account || null);
        setHasRunTask(true);
        conversationSeqRef.current = convo.latest_seq;
      } catch {
        if (!cancelled) {
          setConversation([]);
          setHasRunTask(false);
          setTaskDescription(null);
          setVerificationAgent(null);
        }
      }
    })();

    return () => { cancelled = true; };
  }, [api, sessionId]);

  // Load default coding account
  useEffect(() => {
    let cancelled = false;
    api.listAccounts().then((res) => {
      if (cancelled) return;
      const coding = res.accounts.find((a) => a.role === "CODING") || res.accounts[0] || null;
      setCodingAccount(coding || null);
    }).catch(() => {
      if (!cancelled) setCodingAccount(null);
    });
    return () => { cancelled = true; };
  }, [api]);

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

  // Append conversation items from streaming events
  useEffect(() => {
    if (events.length === 0) return;

    setConversation((prev) => {
      let updated = [...prev];

      for (const ev of events) {
        const seq = ev.seq ?? 0;
        if (seq && seq <= conversationSeqRef.current) continue;
        const data: any = ev.data || {};
        const evtType = data.type || data.event_type;

        if (evtType === "session_started") {
          updated = [];
          setTaskDescription(data.task_description ?? null);
          setVerificationAgent(data.verification_account ?? null);
          setHasRunTask(true);
          if (seq) conversationSeqRef.current = seq;
          continue;
        }

        const item = mapEventToConversationItem(data, seq);
        if (item) {
          updated.push(item);
          if (seq) {
            conversationSeqRef.current = Math.max(conversationSeqRef.current, seq);
          }
        }
      }

      return updated;
    });
  }, [events, mapEventToConversationItem]);

  // Auto-scroll terminal output (live or historical)
  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [terminalOutput, historicalOutput]);

  // Auto-scroll conversation to bottom when new messages arrive
  useEffect(() => {
    if (convoRef.current) {
      requestAnimationFrame(() => {
        if (convoRef.current) {
          convoRef.current.scrollTop = convoRef.current.scrollHeight;
        }
      });
    }
  }, [conversation]);

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

  const handleTaskStart = useCallback(async (taskDesc: string) => {
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
    setTaskActive(true);
    setShowMerge(false);
    setWasCancelled(false);
    setTaskDescription(taskDesc);
    setConversation([]);
    conversationSeqRef.current = 0;
    setHasRunTask(true);
  }, [api, sessionId, reset]);

  const handleMergeDone = useCallback(() => {
    setShowMerge(false);
    onSessionChange();
  }, [onSessionChange]);

  const handleCancel = useCallback(async () => {
    try {
      await api.cancelSession(sessionId);
      setWasCancelled(true);
    } catch {
      // ignore
    }
  }, [api, sessionId]);

  // Screenshot upload handlers
  const handleFiles = useCallback(async (files: FileList | File[]) => {
    const imageFiles = Array.from(files).filter((f) =>
      f.type.startsWith("image/")
    );
    if (imageFiles.length === 0) return;

    setUploading(true);
    setConversationError(null);

    for (const file of imageFiles) {
      try {
        const result = await api.uploadFile(file);
        const previewUrl = URL.createObjectURL(file);
        setScreenshots((prev) => [
          ...prev,
          { path: result.path, filename: result.filename, previewUrl },
        ]);
      } catch (e) {
        setConversationError(e instanceof Error ? e.message : "Failed to upload screenshot");
      }
    }
    setUploading(false);
  }, [api]);

  const handleDrop = useCallback(
    (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setDragOver(false);
      if (e.dataTransfer.files.length > 0) {
        handleFiles(e.dataTransfer.files);
      }
    },
    [handleFiles]
  );

  const handleDragOver = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
  }, []);

  const removeScreenshot = useCallback((index: number) => {
    setScreenshots((prev) => {
      const removed = prev[index];
      if (removed?.previewUrl) {
        URL.revokeObjectURL(removed.previewUrl);
      }
      return prev.filter((_, i) => i !== index);
    });
  }, []);

  const handleSendMessage = useCallback(async () => {
    if (sending) return;

    // Handle interrupt during task execution
    if (taskActive) {
      setConversationError(null);
      setSending(true);
      try {
        const message = inputText.trim();
        // Send Ctrl+C first to interrupt the agent, then the message if any
        const ctrlC = "\x03";
        const payload = message ? ctrlC + "\n" + message + "\n" : ctrlC + "\n";
        const encodedData = btoa(payload);
        await api.sendInput(sessionId, encodedData);

        // Add the interrupt to the conversation as a special user message
        setConversation((prev) => [
          ...prev,
          {
            seq: conversationSeqRef.current + 1,
            ts: new Date().toISOString(),
            type: "user",
            content: `[Interrupt] ${message || "(interrupted)"}`,
          },
        ]);
        conversationSeqRef.current += 1;

        if (message) setInputText("");
      } catch (e) {
        if (e instanceof Error) {
          setConversationError(e.message);
        } else {
          setConversationError("Failed to send interrupt");
        }
      } finally {
        setSending(false);
      }
      return;
    }

    // Handle normal message (start new task)
    if (!inputText.trim()) return;
    if (!codingAccount) {
      setConversationError("Select a coding agent first");
      return;
    }
    setConversationError(null);
    setSending(true);
    try {
      const session = await api.getSession(sessionId);
      const projectPath = session.project_path || currentProjectPath || defaultProjectPath;
      if (!projectPath) {
        setConversationError("Set a project path first");
        setSending(false);
        return;
      }

      // Capture event log position before the task starts
      try {
        const data = await api.getEvents(sessionId, 0, "session_started");
        streamSinceSeqRef.current = data.latest_seq;
      } catch {
        streamSinceSeqRef.current = undefined;
      }

      const message = inputText.trim();
      await api.startTask(sessionId, {
        project_path: projectPath,
        task_description: message,
        coding_agent: codingAccount.name,
        override_prompt: overrideCodingPrompt || undefined,
        is_followup: hasRunTask,
        screenshots: screenshots.length > 0 ? screenshots.map((s) => s.path) : undefined,
      });

      handleTaskStart(message);
      setInputText("");
      // Clear screenshots after starting task
      screenshots.forEach((s) => URL.revokeObjectURL(s.previewUrl));
      setScreenshots([]);
    } catch (e) {
      if (e instanceof Error) {
        setConversationError(e.message);
      } else {
        setConversationError("Failed to start task");
      }
    } finally {
      setSending(false);
    }
  }, [
    api,
    sessionId,
    inputText,
    sending,
    taskActive,
    codingAccount,
    currentProjectPath,
    defaultProjectPath,
    overrideCodingPrompt,
    hasRunTask,
    handleTaskStart,
    conversationSeqRef,
    screenshots,
  ]);

  const handleInputKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  }, [handleSendMessage]);

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
          {verificationAgent && (
            <span className="verification-agent-badge">Verification: {verificationAgent}</span>
          )}
        </div>
      )}

      {/* Project settings (collapsible) - always shown */}
      <ProjectSettings
        api={api}
        projectPath={currentProjectPath || defaultProjectPath}
        onProjectPathChange={handleProjectPathChange}
        onPromptsChange={setOverrideCodingPrompt}
      />

      <div className="chat-body">
        {/* Conversation (takes more space) */}
        <div className="chat-shell">
          <div className="chat-frame">
            <div className="chat-header">
              <div className="chat-agent-picker">
                <span className="field-label">Coding Agent</span>
                <AccountPicker api={api} selected={codingAccount} onSelect={setCodingAccount} />
              </div>
              <div className="chat-status">{taskActive ? "Running…" : hasRunTask ? "Ready for follow-up" : "Ready to start"}</div>
            </div>

            <div className="chat-messages" ref={convoRef}>
              {conversation.map((item) => {
                const isInterrupt = item.type === "user" && item.content?.startsWith("[Interrupt]");
                const label = item.type === "user" ? (isInterrupt ? "Interrupt" : "Pleb") : item.type === "assistant" ? "Agent" : item.title || "Milestone";
                const content = item.type === "milestone" ? (item.summary || "") : (isInterrupt ? item.content?.replace("[Interrupt] ", "") || "" : item.content || "");
                const align = item.type === "user" ? "end" : item.type === "assistant" ? "start" : "center";
                const bubbleClass = isInterrupt ? "user interrupt" : item.type;
                return (
                  <div key={item.seq} className={`chat-item ${align}`}>
                    <div className={`chat-bubble ${bubbleClass}`}>
                      <div className="chat-bubble-label">{label}</div>
                      <div className="chat-bubble-text">{content}</div>
                    </div>
                  </div>
                );
              })}
            </div>

            <div
              className={`chat-composer ${dragOver ? "drag-over" : ""}`}
              onDrop={handleDrop}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
            >
              {/* Screenshot previews */}
              {screenshots.length > 0 && (
                <div className="screenshot-previews">
                  {screenshots.map((s, i) => (
                    <div key={s.path} className="screenshot-preview">
                      <img src={s.previewUrl} alt={s.filename} />
                      <button
                        type="button"
                        className="screenshot-remove"
                        onClick={() => removeScreenshot(i)}
                        title="Remove"
                      >
                        x
                      </button>
                      <span className="screenshot-name">{s.filename}</span>
                    </div>
                  ))}
                </div>
              )}
              <textarea
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                onKeyDown={handleInputKeyDown}
                placeholder={taskActive ? "Type a clarification or additional context for the agent…" : "Type a task or follow-up message (drop images here)"}
                disabled={sending || uploading}
                rows={5}
              />
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                multiple
                style={{ display: "none" }}
                onChange={(e) => e.target.files && handleFiles(e.target.files)}
              />
              <div className="composer-actions">
                {conversationError && <span className="error-text">{conversationError}</span>}
                <div className="composer-right">
                  {uploading && <span className="running-indicator">Uploading…</span>}
                  {taskActive && <span className="running-indicator">Running…</span>}
                  {!taskActive && (
                    <button
                      type="button"
                      onClick={() => fileInputRef.current?.click()}
                      disabled={uploading || sending}
                      className="attach-btn"
                      title="Attach screenshots"
                    >
                      Attach
                    </button>
                  )}
                  <button
                    onClick={handleSendMessage}
                    disabled={sending || uploading || (!taskActive && !inputText.trim())}
                  >
                    {sending ? "Sending..." : taskActive ? (inputText.trim() ? "Send Interrupt" : "Interrupt") : hasRunTask ? "Send follow-up" : "Start task"}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Terminal output (live or historical) */}
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
            {(completed || (historicalOutput && !taskActive)) && (
              <span className={wasCancelled ? "cancelled-indicator" : "done-indicator"}>
                {wasCancelled ? "Cancelled" : "Completed"}
              </span>
            )}
            {error && <span className="error-text">{error}</span>}
          </div>

          <pre ref={outputRef} className="terminal-output">
            {stripAnsi(displayOutput)}
          </pre>
        </div>
      </div>

      {/* Merge panel - show when task completes with changes */}
      {showMerge && !taskActive && (
        <MergePanel
          api={api}
          sessionId={sessionId}
          onMerged={handleMergeDone}
          onDismiss={handleMergeDone}
        />
      )}
    </div>
  );
}
