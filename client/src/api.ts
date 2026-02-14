import type {
  Account,
  AccountCreate,
  AccountList,
  AccountModels,
  CleanupSettings,
  DiffFull,
  DiffSummary,
  MergeResult,
  ProviderList,
  ServerStatus,
  Session,
  SessionCancel,
  SessionCreate,
  SessionList,
  TaskCreate,
  TaskStatus,
  UserPreferences,
  VerificationSettings,
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
  constructor(private baseUrl: string) {
    // Strip trailing slash
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  // ── helpers ──

  private async request<T>(
    path: string,
    options: RequestInit = {},
  ): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      headers: { "Content-Type": "application/json", ...options.headers },
      ...options,
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
    return this.get("/status");
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
  ): Promise<MergeResult> {
    return this.post(`/api/v1/sessions/${sessionId}/worktree/merge`, {
      target_branch: targetBranch ?? null,
    });
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

  // ── Config: Slack ──

  getSlackSettings(): Promise<{
    enabled: boolean;
    channel: string | null;
    has_token: boolean;
    has_signing_secret: boolean;
  }> {
    return this.get("/api/v1/config/slack");
  }

  setSlackSettings(
    settings: Partial<{
      enabled: boolean;
      channel: string;
      bot_token: string;
      signing_secret: string;
    }>,
  ): Promise<{
    enabled: boolean;
    channel: string | null;
    has_token: boolean;
    has_signing_secret: boolean;
  }> {
    return this.put("/api/v1/config/slack", settings);
  }
}
