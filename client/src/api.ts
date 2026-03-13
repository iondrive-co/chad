import type {
  Account,
  AccountCreate,
  AccountList,
  AccountModels,
  AccountUsage,
  AutoconfigureResult,
  AutoconfigureStart,
  BranchesResponse,
  CleanupSettings,
  DiffFull,
  DiffSummary,
  MergeResult,
  ConversationResponse,
  PreviewTunnelStatus,
  ProviderList,
  ServerStatus,
  Session,
  SessionCancel,
  SessionCreate,
  SessionList,
  SessionResume,
  TaskCreate,
  TaskStatus,
  UserPreferences,
  VerificationSettings,
  WebSocketTicket,
  WorktreeStatus,
} from "./types.js";

export class ChadAPIError extends Error {
  constructor(
    public status: number,
    public body: unknown,
  ) {
    super(`HTTP ${status}`);
    this.name = "ChadAPIError";
  }
}

export class ChadAPI {
  private baseUrl: string;
  private token: string | null;

  constructor(baseUrl: string = "http://localhost:3184", token?: string) {
    // Strip trailing slash
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.token = token ?? null;
  }

  // ── helpers ──

  private async request<T>(
    path: string,
    options: RequestInit = {},
  ): Promise<T> {
    const headers: Record<string, string> = {
      ...(options.headers as Record<string, string>),
    };
    // Only set Content-Type when there's a body — setting it on GET/DELETE
    // forces a CORS preflight which breaks cross-origin access through tunnels.
    if (options.body != null) {
      headers["Content-Type"] ??= "application/json";
    }
    if (this.token) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }
    const res = await fetch(`${this.baseUrl}${path}`, {
      ...options,
      headers,
    });
    if (!res.ok) {
      const body = await res.text().catch(() => null);
      throw new ChadAPIError(res.status, body);
    }
    if (res.status === 204) return undefined as T;
    return res.json() as Promise<T>;
  }

  private get<T>(path: string): Promise<T> {
    return this.request(path);
  }

  private post<T>(path: string, body?: unknown): Promise<T> {
    return this.request(path, {
      method: "POST",
      body: body != null ? JSON.stringify(body) : undefined,
    });
  }

  private put<T>(path: string, body: unknown): Promise<T> {
    return this.request(path, { method: "PUT", body: JSON.stringify(body) });
  }

  private del<T>(path: string): Promise<T> {
    return this.request(path, { method: "DELETE" });
  }

  // ── Status ──

  getStatus(): Promise<ServerStatus> {
    // /status is the health-check endpoint exempt from auth.  Skip the
    // Authorization header so the request stays a CORS "simple request"
    // (no preflight) — critical for first-contact through tunnels.
    return fetch(`${this.baseUrl}/status`)
      .then((res) => {
        if (!res.ok) throw new ChadAPIError(res.status, null);
        return res.json() as Promise<ServerStatus>;
      });
  }

  // ── Sessions ──

  createSession(params?: SessionCreate): Promise<Session> {
    return this.post("/api/v1/sessions", params ?? {});
  }

  listSessions(): Promise<SessionList> {
    return this.get("/api/v1/sessions");
  }

  getSession(sessionId: string): Promise<Session> {
    return this.get(`/api/v1/sessions/${sessionId}`);
  }

  deleteSession(sessionId: string): Promise<void> {
    return this.del(`/api/v1/sessions/${sessionId}`);
  }

  cancelSession(sessionId: string): Promise<SessionCancel> {
    return this.post(`/api/v1/sessions/${sessionId}/cancel`);
  }

  resumeSession(sessionId: string): Promise<SessionResume> {
    return this.post(`/api/v1/sessions/${sessionId}/resume`);
  }

  // ── Tasks ──

  startTask(sessionId: string, task: TaskCreate): Promise<TaskStatus> {
    return this.post(`/api/v1/sessions/${sessionId}/tasks`, task);
  }

  getTaskStatus(sessionId: string, taskId: string): Promise<TaskStatus> {
    return this.get(`/api/v1/sessions/${sessionId}/tasks/${taskId}`);
  }

  // ── Messages & Input ──

  sendMessage(
    sessionId: string,
    content: string,
    source = "ui",
  ): Promise<{ success: boolean; session_id: string }> {
    return this.post(`/api/v1/sessions/${sessionId}/messages`, {
      content,
      source,
    });
  }

  sendInput(
    sessionId: string,
    data: string,
  ): Promise<{ success: boolean }> {
    return this.post(`/api/v1/sessions/${sessionId}/input`, { data });
  }

  resizeTerminal(
    sessionId: string,
    rows: number,
    cols: number,
  ): Promise<{ success: boolean; rows: number; cols: number }> {
    return this.post(`/api/v1/sessions/${sessionId}/resize`, { rows, cols });
  }

  getWebSocketTicket(sessionId: string): Promise<WebSocketTicket> {
    return this.post(`/api/v1/ws-ticket/${sessionId}`);
  }

  // ── Events & Milestones ──

  getEvents(
    sessionId: string,
    sinceSeq = 0,
    eventTypes?: string,
  ): Promise<{ events: unknown[]; latest_seq: number; session_id: string }> {
    const params = new URLSearchParams({ since_seq: String(sinceSeq) });
    if (eventTypes) params.set("event_types", eventTypes);
    return this.get(`/api/v1/sessions/${sessionId}/events?${params}`);
  }

  getMilestones(
    sessionId: string,
    sinceSeq = 0,
  ): Promise<{ milestones: unknown[]; latest_seq: number }> {
    return this.get(
      `/api/v1/sessions/${sessionId}/milestones?since_seq=${sinceSeq}`,
    );
  }

  getConversation(
    sessionId: string,
    sinceSeq = 0,
  ): Promise<ConversationResponse> {
    return this.get(
      `/api/v1/sessions/${sessionId}/conversation?since_seq=${sinceSeq}`,
    );
  }

  // ── Accounts ──

  listAccounts(): Promise<AccountList> {
    return this.get("/api/v1/accounts");
  }

  createAccount(params: AccountCreate): Promise<Account> {
    return this.post("/api/v1/accounts", params);
  }

  getAccount(name: string): Promise<Account> {
    return this.get(`/api/v1/accounts/${encodeURIComponent(name)}`);
  }

  deleteAccount(name: string): Promise<{ account_name: string; deleted: boolean; message: string }> {
    return this.del(`/api/v1/accounts/${encodeURIComponent(name)}`);
  }

  setAccountModel(name: string, model: string): Promise<Account> {
    return this.put(`/api/v1/accounts/${encodeURIComponent(name)}/model`, {
      model,
    });
  }

  setAccountReasoning(name: string, reasoning: string): Promise<Account> {
    return this.put(
      `/api/v1/accounts/${encodeURIComponent(name)}/reasoning`,
      { reasoning },
    );
  }

  setAccountRole(name: string, role: string): Promise<Account> {
    return this.put(`/api/v1/accounts/${encodeURIComponent(name)}/role`, {
      role,
    });
  }

  getAccountModels(name: string): Promise<AccountModels> {
    return this.get(
      `/api/v1/accounts/${encodeURIComponent(name)}/models`,
    );
  }

  getAccountUsage(name: string): Promise<AccountUsage> {
    return this.get(
      `/api/v1/accounts/${encodeURIComponent(name)}/usage`,
    );
  }

  // ── Providers ──

  listProviders(): Promise<ProviderList> {
    return this.get("/api/v1/providers");
  }

  // ── Worktree ──

  createWorktree(sessionId: string): Promise<WorktreeStatus> {
    return this.post(`/api/v1/sessions/${sessionId}/worktree`);
  }

  getWorktreeStatus(sessionId: string): Promise<WorktreeStatus> {
    return this.get(`/api/v1/sessions/${sessionId}/worktree`);
  }

  getDiffSummary(sessionId: string): Promise<DiffSummary> {
    return this.get(`/api/v1/sessions/${sessionId}/worktree/diff`);
  }

  getFullDiff(sessionId: string): Promise<DiffFull> {
    return this.get(`/api/v1/sessions/${sessionId}/worktree/diff/full`);
  }

  mergeWorktree(
    sessionId: string,
    targetBranch?: string | null,
    commitMessage?: string | null,
  ): Promise<MergeResult> {
    return this.post(`/api/v1/sessions/${sessionId}/worktree/merge`, {
      target_branch: targetBranch ?? null,
      commit_message: commitMessage ?? null,
    });
  }

  getBranches(sessionId: string): Promise<BranchesResponse> {
    return this.get(`/api/v1/sessions/${sessionId}/worktree/branches`);
  }

  resolveConflicts(
    sessionId: string,
    useIncoming: boolean,
  ): Promise<MergeResult> {
    return this.post(`/api/v1/sessions/${sessionId}/worktree/resolve-conflicts`, {
      use_incoming: useIncoming,
    });
  }

  abortMerge(sessionId: string): Promise<MergeResult> {
    return this.post(`/api/v1/sessions/${sessionId}/worktree/abort-merge`);
  }

  resetWorktree(
    sessionId: string,
  ): Promise<{ session_id: string; reset: boolean; message: string }> {
    return this.post(`/api/v1/sessions/${sessionId}/worktree/reset`);
  }

  deleteWorktree(
    sessionId: string,
  ): Promise<{ session_id: string; deleted: boolean; message: string }> {
    return this.del(`/api/v1/sessions/${sessionId}/worktree`);
  }

  // ── Config: Verification ──

  getVerificationSettings(): Promise<VerificationSettings> {
    return this.get("/api/v1/config/verification");
  }

  setVerificationSettings(
    settings: Partial<VerificationSettings>,
  ): Promise<VerificationSettings> {
    return this.put("/api/v1/config/verification", settings);
  }

  getVerificationAgent(): Promise<{ account_name: string | null }> {
    return this.get("/api/v1/config/verification-agent");
  }

  setVerificationAgent(
    accountName: string | null,
  ): Promise<{ account_name: string | null }> {
    return this.put("/api/v1/config/verification-agent", {
      account_name: accountName,
    });
  }

  getPreferredVerificationModel(): Promise<{ model: string | null }> {
    return this.get("/api/v1/config/preferred-verification-model");
  }

  setPreferredVerificationModel(
    model: string | null,
  ): Promise<{ model: string | null }> {
    return this.put("/api/v1/config/preferred-verification-model", { model });
  }

  getMaxVerificationAttempts(): Promise<{ attempts: number }> {
    return this.get("/api/v1/config/max-verification-attempts");
  }

  setMaxVerificationAttempts(
    attempts: number,
  ): Promise<{ attempts: number }> {
    return this.put("/api/v1/config/max-verification-attempts", { attempts });
  }

  // ── Config: Cleanup ──

  getCleanupSettings(): Promise<CleanupSettings> {
    return this.get("/api/v1/config/cleanup");
  }

  setCleanupSettings(
    settings: Partial<CleanupSettings>,
  ): Promise<CleanupSettings> {
    return this.put("/api/v1/config/cleanup", settings);
  }

  // ── Config: Preferences ──

  getPreferences(): Promise<UserPreferences> {
    return this.get("/api/v1/config/preferences");
  }

  setPreferences(
    prefs: Partial<UserPreferences>,
  ): Promise<UserPreferences> {
    return this.put("/api/v1/config/preferences", prefs);
  }

  // ── Config: Action Settings ──

  getActionSettings(): Promise<{ settings: unknown[] }> {
    return this.get("/api/v1/config/action-settings");
  }

  setActionSettings(
    settings: unknown[],
  ): Promise<{ settings: unknown[] }> {
    return this.put("/api/v1/config/action-settings", { settings });
  }

  // ── Tunnel ──

  getTunnelStatus(): Promise<{
    running: boolean;
    url: string | null;
    subdomain: string | null;
    error: string | null;
  }> {
    return this.get("/api/v1/tunnel");
  }

  startTunnel(): Promise<{
    running: boolean;
    url: string | null;
    subdomain: string | null;
    error: string | null;
  }> {
    return this.post("/api/v1/tunnel/start");
  }

  stopTunnel(): Promise<{
    running: boolean;
    url: string | null;
    subdomain: string | null;
    error: string | null;
  }> {
    return this.post("/api/v1/tunnel/stop");
  }

  // ── Preview Tunnel ──

  getPreviewTunnelStatus(): Promise<PreviewTunnelStatus> {
    return this.get("/api/v1/preview-tunnel");
  }

  startPreviewTunnel(
    port: number,
    options?: {
      command?: string;
      session_id?: string;
      tunnel?: boolean;
    },
  ): Promise<PreviewTunnelStatus> {
    return this.post("/api/v1/preview-tunnel/start", {
      port,
      ...options,
    });
  }

  stopPreviewTunnel(): Promise<PreviewTunnelStatus> {
    return this.post("/api/v1/preview-tunnel/stop");
  }

  // ── Config: Slack ──

  getSlackSettings(): Promise<{
    enabled: boolean;
    channel: string | null;
    has_token: boolean;
  }> {
    return this.get("/api/v1/config/slack");
  }

  setSlackSettings(
    settings: Partial<{
      enabled: boolean;
      channel: string;
      bot_token: string;
    }>,
  ): Promise<{
    enabled: boolean;
    channel: string | null;
    has_token: boolean;
  }> {
    return this.put("/api/v1/config/slack", settings);
  }

  // ── Config: Project Settings ──

  getProjectSettings(
    projectPath: string,
  ): Promise<{
    project_path: string;
    project_type: string | null;
    lint_command: string | null;
    test_command: string | null;
    instructions_paths: string[];
    preview_port: number | null;
    preview_command: string | null;
  }> {
    return this.get(
      `/api/v1/config/project?project_path=${encodeURIComponent(projectPath)}`,
    );
  }

  setProjectSettings(
    settings: {
      project_path: string;
      lint_command?: string | null;
      test_command?: string | null;
      instructions_paths?: string[] | null;
      preview_port?: number | null;
      preview_command?: string | null;
    },
  ): Promise<{
    project_path: string;
    project_type: string | null;
    lint_command: string | null;
    test_command: string | null;
    instructions_paths: string[];
    preview_port: number | null;
    preview_command: string | null;
  }> {
    return this.put("/api/v1/config/project", settings);
  }

  // ── Config: Project Autoconfigure ──

  startAutoconfigure(
    projectPath: string,
    codingAgent: string,
  ): Promise<AutoconfigureStart> {
    return this.post("/api/v1/config/project/autoconfigure", {
      project_path: projectPath,
      coding_agent: codingAgent,
    });
  }

  getAutoconfigureResult(
    jobId: string,
  ): Promise<AutoconfigureResult> {
    return this.get(`/api/v1/config/project/autoconfigure/${jobId}`);
  }

  cancelAutoconfigure(
    jobId: string,
  ): Promise<AutoconfigureResult> {
    return this.post(`/api/v1/config/project/autoconfigure/${jobId}/cancel`);
  }

  getPromptPreviews(
    projectPath?: string,
  ): Promise<{ coding: string; verification: string }> {
    const params = projectPath
      ? `?project_path=${encodeURIComponent(projectPath)}`
      : "";
    return this.get(`/api/v1/config/prompt-previews${params}`);
  }

  // ── Config Export / Import ──

  exportConfig(): Promise<Record<string, unknown>> {
    return this.get("/api/v1/config/export");
  }

  importConfig(
    config: Record<string, unknown>,
  ): Promise<{ ok: boolean; message: string; install_errors?: Record<string, string> }> {
    return this.post("/api/v1/config/import", { config });
  }

  // ── Session Log ──

  getSessionLog(
    sessionId: string,
  ): Promise<{
    session_id: string;
    log_path: string | null;
    log_exists: boolean;
  }> {
    return this.get(`/api/v1/sessions/${sessionId}/log`);
  }

  // ── File Uploads ──

  async uploadFile(file: File): Promise<{ path: string; filename: string }> {
    const formData = new FormData();
    formData.append("file", file);

    const headers: Record<string, string> = {};
    if (this.token) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }

    const res = await fetch(`${this.baseUrl}/api/v1/uploads`, {
      method: "POST",
      headers,
      body: formData,
    });

    if (!res.ok) {
      const body = await res.text().catch(() => null);
      throw new ChadAPIError(res.status, body);
    }

    return res.json();
  }
}
