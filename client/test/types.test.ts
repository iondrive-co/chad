/**
 * Compile-time type tests for chad-client.
 *
 * These don't run at runtime — they verify that the TypeScript types
 * compile correctly and are assignable as expected.
 *
 * Run: npx tsc --noEmit test/types.test.ts
 */

import type {
  Account,
  Session,
  SessionCreate,
  TaskCreate,
  TaskState,
  StreamEvent,
  WSMessage,
  ProviderType,
  RoleType,
} from "../src/index.js";

// ── Session types ──

const session: Session = {
  id: "abc-123",
  name: "Test",
  project_path: "/home/user/project",
  active: true,
  has_worktree: false,
  has_changes: false,
  created_at: "2025-01-01T00:00:00Z",
  last_activity: "2025-01-01T00:00:00Z",
};

const sessionCreate: SessionCreate = {};
const sessionCreateFull: SessionCreate = {
  name: "My Session",
  project_path: "/tmp",
};

// ── Task types ──

const taskCreate: TaskCreate = {
  project_path: "/home/user/project",
  task_description: "Fix the bug",
  coding_agent: "claude-1",
};

const states: TaskState[] = [
  "pending",
  "running",
  "completed",
  "failed",
  "cancelled",
];

// ── Account types ──

const account: Account = {
  name: "claude-1",
  provider: "anthropic",
  model: "claude-sonnet-4-5-20250929",
  reasoning: null,
  role: "CODING",
  ready: true,
};

const providers: ProviderType[] = [
  "anthropic",
  "openai",
  "gemini",
  "qwen",
  "mistral",
  "opencode",
  "kimi",
  "mock",
];

const roles: RoleType[] = ["CODING", "VERIFICATION"];

// ── Stream types ──

const streamEvent: StreamEvent = {
  event_type: "terminal",
  data: { chunk: "hello" },
  seq: 42,
};

// ── WebSocket types ──

const wsMessage: WSMessage = {
  type: "terminal",
  session_id: "abc-123",
  data: { output: "test" },
};

// Suppress unused variable warnings
void session;
void sessionCreate;
void sessionCreateFull;
void taskCreate;
void states;
void account;
void providers;
void roles;
void streamEvent;
void wsMessage;
