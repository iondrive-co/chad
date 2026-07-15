import { useState, useCallback, useRef, useEffect, DragEvent, UIEvent } from "react";
import type { ChadAPI, ConversationItem, Account, ProviderInfo, VerificationSettings, ProjectSettings, StreamEvent } from "chad-client";
import { useStream } from "../hooks/useStream.ts";
import type { TerminalChunk } from "../hooks/useStream.ts";
import { buildTranscript } from "../lib/transcript.ts";
import { MergePanel } from "./MergePanel.tsx";
import { WorktreeInfo } from "./WorktreeInfo.tsx";
import { SessionLog } from "./SessionLog.tsx";
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
  onProjectsChange?: () => Promise<void> | void;
  defaultProjectPath?: string;
  apiBaseUrl?: string;
  token?: string;
  /** Whether the session is active (from polled session list data). */
  sessionActive?: boolean;
  /** Available projects for the project dropdown. */
  projects?: ProjectSettings[];
}

const NEW_PROJECT_VALUE = "__new_project__";

function normalizeLineEndings(text: string): string {
  return text.replace(/\r\n?/g, "\n");
}

function getSessionActivationSinceSeq(events: Array<{ type?: string; seq?: number }>, fallbackSeq: number): number {
  const sessionStarts = events.filter((event) => event.type === "session_started");
  const latestStartSeq = sessionStarts[sessionStarts.length - 1]?.seq;
  if (typeof latestStartSeq === "number") {
    return Math.max(0, latestStartSeq - 1);
  }
  return fallbackSeq;
}

export function ChatView({
  api,
  sessionId,
  onSessionChange,
  onProjectsChange,
  defaultProjectPath = "",
  apiBaseUrl,
  token,
  sessionActive = false,
  projects = [],
}: Props) {
  const [taskActive, setTaskActive] = useState(false);
  const [sending, setSending] = useState(false);
  const [showMerge, setShowMerge] = useState(false);
  const [codingAccount, setCodingAccount] = useState<Account | null>(null);
  // Provider metadata (used to decide whether the coding agent supports a
  // reasoning level) and the reasoning level chosen for the next answer.
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [codingReasoning, setCodingReasoning] = useState("");
  // Whether Slack integration is configured (controls the "post to Slack"
  // toggle) and whether the next task should post its milestones to Slack.
  const [slackEnabled, setSlackEnabled] = useState(false);
  const [postToSlack, setPostToSlack] = useState(true);
  // Models available for the selected coding agent and the per-message override
  // chosen for the next answer ("" = use the account's configured model).
  const [codingModels, setCodingModels] = useState<string[]>([]);
  const [codingModel, setCodingModel] = useState("");
  // The agent + model this session already runs its tasks with. Used to seed
  // the composer so continuing a session (including after a server restart)
  // reuses its own agent instead of a global default. Null for a brand-new
  // session that has not run a task yet.
  const [sessionCodingAgent, setSessionCodingAgent] = useState<string | null>(null);
  const [sessionCodingModel, setSessionCodingModel] = useState<string | null>(null);
  const [conversation, setConversation] = useState<ConversationItem[]>([]);
  const [conversationError, setConversationError] = useState<string | null>(null);
  const conversationSeqRef = useRef(0);
  const [inputText, setInputText] = useState("");
  const [hasRunTask, setHasRunTask] = useState(false);
  const [pendingFollowup, setPendingFollowup] = useState<string | null>(null);
  // Track how the session ended: null (still running or no task), "completed", "cancelled", "timeout", "failed", etc.
  const [endReason, setEndReason] = useState<string | null>(null);
  const [expandedMilestones, setExpandedMilestones] = useState<Set<number>>(new Set());
  const outputRef = useRef<HTMLDivElement>(null);
  // True once the user scrolls up off the bottom; suppresses terminal autoscroll
  // until they return to the bottom, just like a real terminal.
  const userScrolledUpRef = useRef(false);
  // True when content is scrolled past the top edge, so the half-clipped first
  // line gets a fade instead of looking abruptly cut off.
  const [clippedTop, setClippedTop] = useState(false);
  const convoRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Screenshot attachments for task creation
  const [screenshots, setScreenshots] = useState<UploadedScreenshot[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  // Historical transcript loaded from persisted log for finished sessions.
  // Mirrors the live stream: prose chunks (with seq) plus tool_call_started events.
  const [historicalChunks, setHistoricalChunks] = useState<TerminalChunk[]>([]);
  const [historicalEvents, setHistoricalEvents] = useState<StreamEvent[]>([]);

  // Current task description, verification agent, and screenshots (extracted from session_started events)
  const [taskDescription, setTaskDescription] = useState<string | null>(null);
  const [verificationAgent, setVerificationAgent] = useState<string | null>(null);
  const [taskScreenshots, setTaskScreenshots] = useState<string[]>([]);

  // Track current project path for settings
  const [currentProjectPath, setCurrentProjectPath] = useState(defaultProjectPath);
  const [creatingProject, setCreatingProject] = useState(false);
  const [newProjectPath, setNewProjectPath] = useState("");
  const [savingProject, setSavingProject] = useState(false);
  const [projectCreateError, setProjectCreateError] = useState<string | null>(null);
  const [worktreeRefresh, setWorktreeRefresh] = useState(0);

  // Sync when parent changes defaultProjectPath (e.g. selecting a session tab)
  useEffect(() => {
    if (defaultProjectPath) {
      setCurrentProjectPath(defaultProjectPath);
      setCreatingProject(false);
    }
  }, [defaultProjectPath]);

  // Preview
  const [previewPortMode, setPreviewPortMode] = useState<"disabled" | "auto" | "manual">("disabled");
  const [previewPort, setPreviewPort] = useState<number | null>(null);
  const [previewCommand, setPreviewCommand] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  // Verification agent selection for new tasks
  const [verificationAccount, setVerificationAccount] = useState<Account | null>(null);
  const [verificationSettings, setVerificationSettings] = useState<VerificationSettings | null>(null);
  const verificationDefaultsApplied = useRef(false);

  // Track the event log position at which the current task started, so the
  // stream skips old milestones/events from previous tasks in the same session.
  const streamSinceSeqRef = useRef<number | undefined>(undefined);

  const { terminalChunks, events, completed, error, reset } = useStream(
    taskActive ? sessionId : null,
    streamSinceSeqRef.current,
    apiBaseUrl,
    token,
  );

  // Live stream (during a task) or historical log (finished session). The right
  // panel renders a Claude-Code-style transcript from these — tool calls plus
  // prose — rather than the raw terminal text.
  const usingLive = taskActive || terminalChunks.length > 0 || events.length > 0;
  const transcript = buildTranscript(
    usingLive ? terminalChunks : historicalChunks,
    usingLive ? events : historicalEvents,
  );
  const hasOutput = transcript.length > 0;
  const hasHistorical = historicalChunks.length > 0 || historicalEvents.length > 0;

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

  // Load historical transcript when session is selected and not active
  useEffect(() => {
    let cancelled = false;
    setHistoricalChunks([]);
    setHistoricalEvents([]);
    setTaskDescription(null);
    setVerificationAgent(null);

    if (!sessionActive && !taskActive) {
      (async () => {
        try {
          const data = await api.getEvents(sessionId, 0, "terminal_output,tool_call_started,session_started,session_ended");
          if (cancelled) return;

          const allEvents = data.events as { type: string; seq?: number; data?: string }[];

          const chunks: TerminalChunk[] = allEvents
            .filter((e) => e.type === "terminal_output" && e.data)
            .map((e) => ({ text: normalizeLineEndings(e.data || ""), seq: e.seq ?? null }));
          setHistoricalChunks(chunks);

          const toolEvents: StreamEvent[] = allEvents
            .filter((e) => e.type === "tool_call_started")
            .map((e) => ({ event_type: "event", data: e, seq: e.seq ?? null }));
          setHistoricalEvents(toolEvents);

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

          // Track the session end reason for status display
          const ends = (data.events as { type: string; reason?: string }[])
            .filter((e) => e.type === "session_ended");
          if (ends.length > 0) {
            const lastEnd = ends[ends.length - 1];
            setEndReason(lastEnd.reason || "completed");
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
        setTaskScreenshots((convo.task as { screenshots?: string[] }).screenshots || []);
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

  // Load this session's own coding agent/model (set once it has run a task)
  // so the composer can reuse it instead of a global default.
  useEffect(() => {
    let cancelled = false;
    setSessionCodingAgent(null);
    setSessionCodingModel(null);
    api.getSession(sessionId)
      .then((s) => {
        if (cancelled) return;
        setSessionCodingAgent(s.coding_account);
        setSessionCodingModel(s.coding_model);
      })
      .catch(() => { /* new/unknown session: fall back to defaults */ });
    return () => { cancelled = true; };
  }, [api, sessionId]);

  // Load default coding account, using project's preferred agent if available
  useEffect(() => {
    let cancelled = false;
    api.listAccounts().then((res) => {
      if (cancelled) return;

      // Reuse the agent this session already runs with, so continuing a
      // session keeps its own agent rather than resetting to a global default.
      if (sessionCodingAgent) {
        const sessionAccount = res.accounts.find((a) => a.name === sessionCodingAgent);
        if (sessionAccount) {
          setCodingAccount(sessionAccount);
          return;
        }
      }

      // Find the project's preferred coding agent if a project is selected
      const currentProject = projects?.find((p) => p.project_path === currentProjectPath);
      const preferredAgentName = currentProject?.preferred_coding_agent;

      // Try to use the project's preferred agent first
      if (preferredAgentName) {
        const preferredAccount = res.accounts.find((a) => a.name === preferredAgentName);
        if (preferredAccount) {
          setCodingAccount(preferredAccount);
          return;
        }
      }

      // Fall back to the global CODING role or first account
      const coding = res.accounts.find((a) => a.role === "CODING") || res.accounts[0] || null;
      setCodingAccount(coding || null);
    }).catch(() => {
      if (!cancelled) setCodingAccount(null);
    });
    return () => { cancelled = true; };
  }, [api, currentProjectPath, projects, sessionCodingAgent]);

  // Load provider metadata so we know which coding agents support a reasoning level.
  useEffect(() => {
    let cancelled = false;
    api.listProviders()
      .then((r) => { if (!cancelled) setProviders(r.providers); })
      .catch(() => { if (!cancelled) setProviders([]); });
    return () => { cancelled = true; };
  }, [api]);

  // Learn whether Slack is configured so the composer can offer (or grey out)
  // the per-task "post to Slack" toggle.
  useEffect(() => {
    let cancelled = false;
    api.getSlackSettings()
      .then((s) => { if (!cancelled) setSlackEnabled(s.enabled); })
      .catch(() => { if (!cancelled) setSlackEnabled(false); });
    return () => { cancelled = true; };
  }, [api]);

  // Load the models the selected coding agent can run so the composer can offer
  // a per-message model override. When the selected agent is the one this
  // session runs with, restore its saved model; otherwise clear the override so
  // a stale selection can't leak onto a different account.
  useEffect(() => {
<<<<<<< Updated upstream
    setCodingModel("");
    // Reasoning levels differ per provider, so a level chosen for one agent may
    // not exist for the next — reset to the provider default on agent change.
    setCodingReasoning("");
=======
>>>>>>> Stashed changes
    if (!codingAccount) {
      setCodingModel("");
      setCodingModels([]);
      return;
    }
    setCodingModel(
      sessionCodingAgent === codingAccount.name ? (sessionCodingModel ?? "") : "",
    );
    let cancelled = false;
    api.getAccountModels(codingAccount.name)
      .then((r) => { if (!cancelled) setCodingModels(r.models); })
      .catch(() => { if (!cancelled) setCodingModels([]); });
    return () => { cancelled = true; };
  }, [api, codingAccount, sessionCodingAgent, sessionCodingModel]);

  // Load verification settings and default verification agent
  useEffect(() => {
    let cancelled = false;

    api.getVerificationSettings()
      .then((settings) => {
        if (cancelled) return;
        setVerificationSettings(settings);
        // On first load, if verification is disabled clear the account
        if (!settings.enabled) {
          setVerificationAccount(null);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setVerificationSettings({ enabled: true });
        }
      });

    api.getVerificationAgent()
      .then((r) => {
        if (cancelled) return;
        const name = r.account_name;
        if (!name || name === "__verification_none__") return;
        if (verificationDefaultsApplied.current) return;
        api.getAccount(name)
          .then((acct) => {
            if (!cancelled) {
              setVerificationAccount(acct);
              verificationDefaultsApplied.current = true;
            }
          })
          .catch(() => { /* ignore missing account */ });
      })
      .catch(() => {});

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
          if (!cancelled) {
            streamSinceSeqRef.current = getSessionActivationSinceSeq(
              data.events as Array<{ type?: string; seq?: number }>,
              data.latest_seq,
            );
          }
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
          // Preserve conversation on follow-up (when prev has items)
          // Only clear on first task start
          if (prev.length === 0) {
            updated = [];
          }
          setTaskDescription(data.task_description ?? null);
          setVerificationAgent(data.verification_account ?? null);
          setTaskScreenshots(data.screenshots ?? []);
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

  // Track whether the user has scrolled up off the bottom of the transcript.
  // While scrolled up, new output does not yank them back down.
  const handleTerminalScroll = useCallback((e: UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    userScrolledUpRef.current = distanceFromBottom > 24;
    setClippedTop(el.scrollTop > 4);
  }, []);

  // Auto-scroll terminal transcript to the bottom as it grows, unless the user
  // has scrolled up to read back — matching real terminal behaviour.
  useEffect(() => {
    const el = outputRef.current;
    if (el && !userScrolledUpRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [terminalChunks, events, historicalChunks, historicalEvents]);

  // Reset scroll-follow state when switching sessions.
  useEffect(() => {
    userScrolledUpRef.current = false;
    setClippedTop(false);
  }, [sessionId]);

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

  // Mark task inactive when stream completes, check for worktree changes and end reason
  useEffect(() => {
    if (completed) {
      setTaskActive(false);
      onSessionChange();
      // Fetch the session end reason and check for worktree changes
      Promise.all([
        api.getEvents(sessionId, 0, "session_ended").then((data) => {
          const ends = (data.events as { type: string; reason?: string; success?: boolean }[])
            .filter((e) => e.type === "session_ended");
          if (ends.length > 0) {
            const lastEnd = ends[ends.length - 1];
            setEndReason(lastEnd.reason || "completed");
          } else {
            setEndReason("completed");
          }
        }).catch(() => {
          setEndReason("completed");
        }),
        api.getWorktreeStatus(sessionId).then((status) => {
          if (status.exists && status.has_changes) {
            setShowMerge(true);
          }
        }).catch(() => {}),
      ]);
    }
  }, [api, completed, sessionId, onSessionChange]);

  const handleTaskStart = useCallback(async (taskDesc: string, isFollowup: boolean = false) => {
    // Capture the current event log position before the task starts, so the
    // stream only shows events from this task (not old milestones/output).
    try {
      const data = await api.getEvents(sessionId, 0, "session_started");
      streamSinceSeqRef.current = data.latest_seq;
    } catch {
      streamSinceSeqRef.current = undefined;
    }
    reset();
    // Clear historical transcript when starting a new task
    setHistoricalChunks([]);
    setHistoricalEvents([]);
    setTaskActive(true);
    setShowMerge(false);
    setEndReason(null);
    setTaskDescription(taskDesc);
    // Preserve conversation history on follow-up tasks
    if (!isFollowup) {
      setConversation([]);
      conversationSeqRef.current = 0;
    }
    setHasRunTask(true);
  }, [api, sessionId, reset]);

  const handleMergeDone = useCallback(() => {
    setShowMerge(false);
    onSessionChange();
  }, [onSessionChange]);

  const handleCancel = useCallback(async () => {
    try {
      await api.cancelSession(sessionId);
      setEndReason("cancelled");
    } catch {
      // ignore
    }
  }, [api, sessionId]);

  // Determine if we're connected to a remote server (need tunnel) or local (open directly)
  const isRemote = Boolean(apiBaseUrl) && !/^https?:\/\/(localhost|127\.0\.0\.1)(:|\/|$)/.test(apiBaseUrl || "");

  const handlePreview = useCallback(async () => {
    if (previewPortMode === "disabled") return;
    if (previewPortMode === "manual" && !previewPort) return;

    // If already running, just open the URL
    if (previewUrl) {
      window.open(previewUrl, "_blank", "noopener");
      return;
    }

    setPreviewLoading(true);
    try {
      const result = await api.startPreviewTunnel({
        port: previewPort || undefined,
        command: previewCommand || undefined,
        session_id: sessionId,
        tunnel: isRemote,
        autodetect_port: previewPortMode === "auto",
      });

      const url = isRemote && result.url
        ? result.url
        : result.port ? `http://localhost:${result.port}` : undefined;
      if (url) {
        setPreviewUrl(url);
        window.open(url, "_blank", "noopener");
      }
    } catch {
      // ignore
    } finally {
      setPreviewLoading(false);
    }
  }, [api, previewPortMode, previewPort, previewCommand, previewUrl, isRemote, sessionId]);

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

  const handleProjectSelect = useCallback((value: string) => {
    setProjectCreateError(null);
    if (value === NEW_PROJECT_VALUE) {
      setCreatingProject(true);
      setCurrentProjectPath("");
      return;
    }
    setCreatingProject(false);
    setNewProjectPath("");
    setCurrentProjectPath(value);
  }, []);

  const handleAddProjectFromChat = useCallback(async () => {
    const path = newProjectPath.trim();
    if (!path) {
      setProjectCreateError("Enter a project path");
      return;
    }

    setSavingProject(true);
    setProjectCreateError(null);
    try {
      const settings = await api.setProjectSettings({ project_path: path });
      await onProjectsChange?.();
      setCurrentProjectPath(settings.project_path);
      setCreatingProject(false);
      setNewProjectPath("");
    } catch {
      setProjectCreateError("Failed to add project");
    } finally {
      setSavingProject(false);
    }
  }, [api, newProjectPath, onProjectsChange]);

  const removeScreenshot = useCallback((index: number) => {
    setScreenshots((prev) => {
      const removed = prev[index];
      if (removed?.previewUrl) {
        URL.revokeObjectURL(removed.previewUrl);
      }
      return prev.filter((_, i) => i !== index);
    });
  }, []);

  const startTaskRequest = useCallback(async (
    message: string,
    isFollowup: boolean,
    attachedScreenshots: UploadedScreenshot[],
  ) => {
    if (!codingAccount) {
      throw new Error("Select a coding agent first");
    }

    const session = await api.getSession(sessionId);
    const projectPath = session.project_path || currentProjectPath || defaultProjectPath;
    if (!projectPath) {
      throw new Error("Set a project path first");
    }

    try {
      const data = await api.getEvents(sessionId, 0, "session_started");
      streamSinceSeqRef.current = data.latest_seq;
    } catch {
      streamSinceSeqRef.current = undefined;
    }

    const verificationAllowed = verificationSettings?.enabled && verificationAccount;
    await api.startTask(sessionId, {
      project_path: projectPath,
      task_description: message,
      coding_agent: codingAccount.name,
      coding_model: codingModel || undefined,
      coding_reasoning: codingReasoning || undefined,
      verification_agent: verificationAllowed ? verificationAccount.name : undefined,
      is_followup: isFollowup,
      screenshots: attachedScreenshots.length > 0 ? attachedScreenshots.map((s) => s.path) : undefined,
      notify_slack: slackEnabled && postToSlack,
    });

    handleTaskStart(message, isFollowup);
  }, [
    api,
    sessionId,
    codingAccount,
    codingModel,
    codingReasoning,
    verificationAccount,
    verificationSettings,
    currentProjectPath,
    defaultProjectPath,
    handleTaskStart,
    slackEnabled,
    postToSlack,
  ]);

  const handleSendMessage = useCallback(async () => {
    if (sending) return;

    // Handle interrupt during task execution
    if (taskActive) {
      setConversationError(null);
      setSending(true);
      try {
        const message = inputText.trim();
        if (message) {
          await api.cancelSession(sessionId);
          setPendingFollowup(message);
          setInputText("");
        } else {
          const encodedData = btoa("\x03\n");
          await api.sendInput(sessionId, encodedData);

          setConversation((prev) => [
            ...prev,
            {
              seq: conversationSeqRef.current + 1,
              ts: new Date().toISOString(),
              type: "user",
              content: "[Interrupt] (interrupted)",
            },
          ]);
          conversationSeqRef.current += 1;
        }
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
    const message = inputText.trim();
    if (!message) return;

    setConversationError(null);
    setSending(true);
    try {
      await startTaskRequest(message, hasRunTask, screenshots);
      setInputText("");
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
    hasRunTask,
    screenshots,
    startTaskRequest,
  ]);

  useEffect(() => {
    if (!pendingFollowup || taskActive || sending) return;

    let cancelled = false;
    const message = pendingFollowup;
    setPendingFollowup(null);
    setConversationError(null);
    setSending(true);

    (async () => {
      try {
        await startTaskRequest(message, true, []);
      } catch (e) {
        if (cancelled) return;
        setPendingFollowup(message);
        if (e instanceof Error) {
          setConversationError(e.message);
        } else {
          setConversationError("Failed to start follow-up task");
        }
      } finally {
        if (!cancelled) {
          setSending(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [pendingFollowup, taskActive, sending, startTaskRequest]);

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

  // Load preview settings when project path changes
  useEffect(() => {
    if (!currentProjectPath) {
      setPreviewPort(null);
      setPreviewCommand(null);
      return;
    }
    api.getProjectSettings(currentProjectPath).then((s) => {
      setPreviewPortMode(s.preview_port_mode || "disabled");
      setPreviewPort(s.preview_port);
      setPreviewCommand(s.preview_command);
    }).catch(() => {});
  }, [api, currentProjectPath]);

  const projectSelectorValue = creatingProject ? NEW_PROJECT_VALUE : currentProjectPath;
  const showNewProjectForm = creatingProject || projects.length === 0;

  // The reasoning-level dropdown only makes sense for coding agents whose
  // provider supports it, and the available levels vary per provider (Codex has
  // four, Claude Code has more, others have none).
  const codingReasoningLevels =
    providers.find((p) => p.type === codingAccount?.provider)?.reasoning_levels ?? [];
  const codingSupportsReasoning = codingReasoningLevels.length > 0;

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
      {taskDescription && (taskActive || hasOutput) && (
        <div className="task-description-bar">
          <span className="task-description-label">Task:</span>
          <span className="task-description-text">{taskDescription}</span>
          {verificationAgent && (
            <span className="verification-agent-badge">Verification: {verificationAgent}</span>
          )}
          {taskScreenshots.length > 0 && (
            <div className="task-screenshots">
              {taskScreenshots.map((path, i) => (
                <img
                  key={i}
                  src={`/api/v1/file?path=${encodeURIComponent(path)}`}
                  alt={`Screenshot ${i + 1}`}
                  className="task-screenshot-thumbnail"
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Project selector - shown when no task has been run yet */}
      {!hasRunTask && (
        <div className="project-selector-bar">
          {projects.length > 0 ? (
            <label>
              Project
              <select
                value={projectSelectorValue}
                onChange={(e) => handleProjectSelect(e.target.value)}
              >
                <option value="">-- Select a project --</option>
                {projects.map((p) => (
                  <option key={p.project_path} value={p.project_path}>
                    {p.project_path}{p.project_type && p.project_type !== "unknown" ? ` (${p.project_type})` : ""}
                  </option>
                ))}
                <option value={NEW_PROJECT_VALUE}>New project</option>
              </select>
            </label>
          ) : (
            <span className="project-selector-label">Project</span>
          )}
          {showNewProjectForm && (
            <form
              className="project-selector-new"
              onSubmit={(e) => {
                e.preventDefault();
                void handleAddProjectFromChat();
              }}
            >
              <input
                value={newProjectPath}
                onChange={(e) => setNewProjectPath(e.target.value)}
                placeholder="/path/to/project"
                aria-label="New project path"
                disabled={savingProject}
              />
              <button type="submit" disabled={savingProject || !newProjectPath.trim()}>
                {savingProject ? "Adding..." : "Add"}
              </button>
              {projectCreateError && <span className="project-selector-error">{projectCreateError}</span>}
            </form>
          )}
        </div>
      )}

      <div className="chat-body">
        {/* Conversation (takes more space) */}
        <div className="chat-shell">
          <div className="chat-frame">
            <div className="chat-header">
              <div className="chat-agent-pickers">
                <div className="chat-agent-picker">
                  <span className="field-label">Coding Agent</span>
                  <AccountPicker api={api} selected={codingAccount} onSelect={setCodingAccount} autoSelect={false} />
                </div>
                <div className="chat-verification-picker">
                  <span className="field-label">Verification Agent</span>
                  <AccountPicker
                    api={api}
                    selected={verificationAccount}
                    onSelect={setVerificationAccount}
                    placeholder="None"
                    allowNone
                  />
                </div>
              </div>
            </div>

            <div className="chat-messages" ref={convoRef}>
              {conversation.map((item) => {
                const isInterrupt = item.type === "user" && item.content?.startsWith("[Interrupt]");
                const label = item.type === "user" ? (isInterrupt ? "Interrupt" : "Pleb") : item.type === "assistant" ? "Agent" : item.title || "Milestone";
                const content = item.type === "milestone" ? (item.summary || "") : (isInterrupt ? item.content?.replace("[Interrupt] ", "") || "" : item.content || "");
                const align = item.type === "user" ? "end" : item.type === "assistant" ? "start" : "center";
                const bubbleClass = isInterrupt ? "user interrupt" : item.type;
                const isMilestone = item.type === "milestone";
                const isExpanded = isMilestone && expandedMilestones.has(item.seq);
                const toggleExpand = isMilestone ? () => {
                  setExpandedMilestones(prev => {
                    const next = new Set(prev);
                    if (next.has(item.seq)) next.delete(item.seq);
                    else next.add(item.seq);
                    return next;
                  });
                } : undefined;
                return (
                  <div key={item.seq} className={`chat-item ${align}`}>
                    <div
                      className={`chat-bubble ${bubbleClass}${isMilestone ? " clickable" : ""}`}
                      onClick={toggleExpand}
                    >
                      <div className="chat-bubble-label">{label}</div>
                      <div className={`chat-bubble-text${isMilestone && !isExpanded ? " clamped" : ""}`}>{content}</div>
                      {isMilestone && (
                        <div className="chat-bubble-expand-hint">{isExpanded ? "Click to collapse" : "Click to expand"}</div>
                      )}
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
                  {codingModels.length > 1 && (
                    <select
                      className="model-select"
                      value={codingModel}
                      onChange={(e) => setCodingModel(e.target.value)}
                      disabled={sending}
                      aria-label="Model"
                      title="Model to use for the answer"
                    >
                      <option value="">Model: default</option>
                      {codingModels
                        .filter((m) => m !== "default")
                        .map((m) => (
                          <option key={m} value={m}>
                            {`Model: ${m}`}
                          </option>
                        ))}
                    </select>
                  )}
                  {codingSupportsReasoning && (
                    <select
                      className="reasoning-select"
                      value={codingReasoning}
                      onChange={(e) => setCodingReasoning(e.target.value)}
                      disabled={sending}
                      aria-label="Reasoning level"
                      title="Reasoning level to use for the answer"
                    >
                      {["", ...codingReasoningLevels].map((r) => (
                        <option key={r} value={r}>
                          {r ? `Reasoning: ${r}` : "Reasoning: default"}
                        </option>
                      ))}
                    </select>
                  )}
                  <label
                    className="slack-toggle"
                    title={slackEnabled
                      ? "Post milestone updates for this task to Slack"
                      : "Enable Slack in Settings to post task updates"}
                  >
                    <input
                      type="checkbox"
                      className="slack-toggle-checkbox"
                      checked={slackEnabled && postToSlack}
                      disabled={sending || !slackEnabled}
                      onChange={(e) => setPostToSlack(e.target.checked)}
                    />
                    Slack
                  </label>
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
            {(completed || (hasHistorical && !taskActive)) && (
              <span className={endReason === "completed" || !endReason ? "done-indicator" : endReason === "cancelled" ? "cancelled-indicator" : "failed-indicator"}>
                {endReason === "cancelled" ? "Cancelled" : endReason === "timeout" ? "Timed out" : endReason && endReason !== "completed" ? `Failed (${endReason})` : "Completed"}
              </span>
            )}
            {error && <span className="error-text">{error}</span>}
            {previewPortMode !== "disabled" && (
              <button
                className="preview-btn"
                onClick={handlePreview}
                disabled={previewLoading || (previewPortMode === "manual" && !previewPort)}
                title={previewUrl ? `Open preview (${previewUrl})` : previewCommand ? `Start "${previewCommand}"${previewPortMode === "auto" ? " (auto-detect port)" : ` on port ${previewPort}`}` : previewPort ? `Open localhost:${previewPort}` : "Start preview"}
              >
                {previewLoading ? "Starting..." : previewUrl ? "Preview" : "Start Preview"}
              </button>
            )}
          </div>

          <div
            ref={outputRef}
            className={`terminal-output${clippedTop ? " clipped-top" : ""}`}
            onScroll={handleTerminalScroll}
          >
            {transcript.map((line, i) =>
              line.kind === "tool" ? (
                <div key={i} className="tline tool">
                  <span className="tool-glyph">●</span> {line.text}
                </div>
              ) : line.kind === "user" ? (
                <div key={i} className="tline user">
                  <span className="tool-glyph">❯</span> {line.text}
                </div>
              ) : (
                <div key={i} className="tline prose">
                  {line.text}
                </div>
              ),
            )}
          </div>
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
