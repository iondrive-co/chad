// ── Session types ──

export interface Session {
  id: string;
  name: string;
  project_path: string | null;
  active: boolean;
  paused: boolean;
  has_worktree: boolean;
  has_changes: boolean;
  coding_account: string | null;
  task_description: string | null;
  status: "active" | "completed" | "interrupted";
  resumable: boolean;
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

export interface SessionResume {
  session_id: string;
  resumed: boolean;
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
  is_followup?: boolean;
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
  session_usage_pct: number | null;
  weekly_usage_pct: number | null;
  session_reset_eta: string | null;
  weekly_reset_eta: string | null;
}

// ── Config types ──

export interface VerificationSettings {
  enabled: boolean;
}

export interface CleanupSettings {
  cleanup_days: number;
  auto_cleanup: boolean;
}

export interface UserPreferences {
  last_project_path: string | null;
  ui_mode: string;
}

export interface SlackSettings {
  enabled: boolean;
  channel: string | null;
  has_token: boolean;
}

export interface SlackSettingsUpdate {
  enabled?: boolean | null;
  channel?: string | null;
  bot_token?: string | null;
}

// ── Tunnel types ──

export interface TunnelStatus {
  running: boolean;
  url: string | null;
  subdomain: string | null;
  error: string | null;
}

export interface PreviewTunnelStatus {
  running: boolean;
  url: string | null;
  port: number | null;
  error: string | null;
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

export interface BranchesResponse {
  branches: string[];
  default: string;
  current: string;
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

// ── Conversation timeline ──

export type ConversationItemType = "user" | "assistant" | "milestone";

export interface ConversationItem {
  seq: number;
  ts: string;
  type: ConversationItemType;
  content?: string | null;
  blocks?: Array<Record<string, unknown>> | null;
  milestone_type?: string | null;
  title?: string | null;
  summary?: string | null;
}

export interface ConversationTask {
  seq: number;
  task_description: string;
  project_path: string;
  coding_provider: string;
  coding_account: string;
  coding_model: string | null;
  verification_account: string | null;
}

export interface ConversationResponse {
  session_id: string;
  task: ConversationTask;
  items: ConversationItem[];
  latest_seq: number;
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

export interface WebSocketTicket {
  ticket: string;
  expires_in: number;
}

// ── Project settings ──

export interface ProjectSettings {
  project_path: string;
  project_type: string | null;
  lint_command: string | null;
  test_command: string | null;
  instructions_paths: string[];
  preview_port_mode: "disabled" | "auto" | "manual";
  preview_port: number | null;
  preview_command: string | null;
  preferred_coding_agent: string | null;
}

export interface AutoconfigureStart {
  job_id: string;
}

export interface AutoconfigureResult {
  status: "running" | "completed" | "failed";
  settings: ProjectSettings | null;
  error: string | null;
  output: string[];
}

export interface ProjectSettingsUpdate {
  project_path: string;
  lint_command?: string | null;
  test_command?: string | null;
  instructions_paths?: string[] | null;
  preview_port_mode?: "disabled" | "auto" | "manual";
  preview_port?: number | null;
  preview_command?: string | null;
  preferred_coding_agent?: string | null;
}

export interface PromptPreviews {
  coding: string;
  verification: string;
}

// ── Session log ──

export interface SessionLog {
  session_id: string;
  log_path: string | null;
  log_exists: boolean;
}
