// ── Session types ──

export interface Session {
  id: string;
  name: string;
  project_path: string | null;
  active: boolean;
  has_worktree: boolean;
  has_changes: boolean;
  created_at: string;
  last_activity: string;
}

export interface SessionCreate {
  name?: string;
  project_path?: string | null;
}

export interface SessionList {
  sessions: Session[];
  total: number;
}

export interface SessionCancel {
  session_id: string;
  cancel_requested: boolean;
  message: string;
}

// ── Task types ──

export type TaskState =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export interface TaskCreate {
  project_path: string;
  task_description: string;
  coding_agent: string;
  coding_model?: string | null;
  coding_reasoning?: string | null;
  verification_agent?: string | null;
  verification_model?: string | null;
  verification_reasoning?: string | null;
  target_branch?: string | null;
  terminal_rows?: number | null;
  terminal_cols?: number | null;
  screenshots?: string[] | null;
  override_prompt?: string | null;
}

export interface TaskStatus {
  task_id: string;
  session_id: string;
  status: TaskState;
  progress: string | null;
  result: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface TaskFollowup {
  message: string;
}

export interface TaskFollowupResponse {
  task_id: string;
  message_sent: boolean;
  status: TaskState;
}

// ── Provider & Account types ──

export type ProviderType =
  | "anthropic"
  | "openai"
  | "gemini"
  | "qwen"
  | "mistral"
  | "opencode"
  | "kimi"
  | "mock";

export type RoleType = "CODING" | "VERIFICATION";

export interface ProviderInfo {
  type: ProviderType;
  name: string;
  description: string;
  supports_reasoning: boolean;
}

export interface ProviderList {
  providers: ProviderInfo[];
}

export interface Account {
  name: string;
  provider: ProviderType;
  model: string | null;
  reasoning: string | null;
  role: RoleType | null;
  ready: boolean;
}

export interface AccountList {
  accounts: Account[];
  total: number;
}

export interface AccountCreate {
  name: string;
  provider: ProviderType;
}

export interface AccountModels {
  account_name: string;
  provider: ProviderType;
  models: string[];
}

export interface AccountUsage {
  account_name: string;
  provider: ProviderType;
  usage_text: string;
  remaining_capacity: number;
}

// ── Config types ──

export interface VerificationSettings {
  enabled: boolean;
  auto_run: boolean;
}

export interface CleanupSettings {
  cleanup_days: number;
  auto_cleanup: boolean;
}

export interface UserPreferences {
  last_project_path: string | null;
  dark_mode: boolean;
  ui_mode: string;
}

export interface SlackSettings {
  enabled: boolean;
  channel: string | null;
  has_token: boolean;
  has_signing_secret: boolean;
}

export interface SlackSettingsUpdate {
  enabled?: boolean | null;
  channel?: string | null;
  bot_token?: string | null;
  signing_secret?: string | null;
}

// ── Worktree types ──

export interface WorktreeStatus {
  exists: boolean;
  path: string | null;
  branch: string | null;
  base_commit: string | null;
  has_changes: boolean;
}

export interface DiffLine {
  type: "context" | "add" | "delete" | "header";
  content: string;
  old_line: number | null;
  new_line: number | null;
}

export interface DiffHunk {
  old_start: number;
  old_count: number;
  new_start: number;
  new_count: number;
  lines: DiffLine[];
}

export interface FileDiff {
  old_path: string;
  new_path: string;
  is_new: boolean;
  is_deleted: boolean;
  is_binary: boolean;
  hunks: DiffHunk[];
}

export interface DiffSummary {
  summary: string;
  files_changed: number;
  insertions: number;
  deletions: number;
}

export interface DiffFull {
  session_id: string;
  summary: DiffSummary;
  files: FileDiff[];
}

export interface MergeConflictHunk {
  ours: string[];
  theirs: string[];
  base: string[];
}

export interface MergeConflict {
  file_path: string;
  hunks: MergeConflictHunk[];
}

export interface MergeResult {
  success: boolean;
  message: string;
  conflicts: MergeConflict[] | null;
}

// ── Streaming types ──

export type StreamEventType =
  | "terminal"
  | "event"
  | "ping"
  | "complete"
  | "error";

export interface StreamEvent {
  event_type: StreamEventType;
  data: Record<string, unknown>;
  seq: number | null;
}

// ── WebSocket types ──

export type WSClientMessageType = "input" | "resize" | "cancel" | "ping";
export type WSServerMessageType =
  | "terminal"
  | "event"
  | "complete"
  | "error"
  | "pong"
  | "status";

export interface WSMessage {
  type: WSServerMessageType;
  session_id: string;
  data: Record<string, unknown>;
}

// ── Server status ──

export interface ServerStatus {
  status: string;
  version: string;
  uptime_seconds: number;
}
