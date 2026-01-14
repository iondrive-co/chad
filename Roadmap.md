v0.7 Touring complete

Windows support
Integrated live view with stable scrollbar
Screenshots and hypothesis in task summaries
Early initial summary report
Allow continued conversation after merge
Switch from MCP usage to skills
Pycharm launcher with dev mode for starting a mock provider for better testing

v0.8 Paper maximiser

Split ui code from core
API allows for live stream from agent (rather than json)
New text only ui for testing
Simplify tests
Structured log format 

v0.9 Hack-propagation

Full inter-provider handoff including summarize progress and resume from checkpoints
Testing the mistral and gemini providers and add qwen code
Load balance based on usage, user preference, context limits hit, etc.

v0.10 Slip slop slap

Use of /agents
Telegram and slack integrations
Packaging for different platforms
Repeated failures

---- Repeated failures ----

Ask the coding agent to answer the following questions:
- Is this a temporary fix or will it work in the future?
- What about the structure of the code cause so many failed attempts to fix this? 
- How could the code be improved so that fewer attempts are needed for similar issues in the future?
- Is there any redundant code from the failed attempts that could be removed?

----Structured log format-----

Change format of precreated chad_session_*.json

append_event(filepath_base, event_type, payload)

Open file in append mode, write one line, flush+fsync (or at least flush).
Store event_id (uuid), ts, seq (monotonic counter), session_id, turn_id.

Log:

session_started
Includes task_description, project_path, coding provider/account (you already log these)

model_selected / provider_switched
{from_provider, to_provider, from_model, to_model, reason}

user_message / assistant_message
Assistant message should allow blocks: {kind:"text"|"thinking"|"tool_call" ...}

tool_declared
Snapshot tool schema (name, args schema, version). This lets a different agent backend know what tools exist.

tool_call_started (local machine)
For commands: {tool:"bash", cwd, command, env_redactions, timeout_s}
For file reads/writes: {path, bytes, sha256}
For MCP tools: {server, tool_name, args}

tool_call_finished
{exit_code, duration_ms, stdout_ref, stderr_ref, llm_summary}
Store full stdout/stderr in artifacts (or inline if small), but always include an llm_summary that is bounded.

verification_attempt
Replace your current “verification_attempts list” with events that point to concrete tool invocations and results.

context_condensed
{replaces_seq_range, summary_text, policy} (when context becomes too large)


For commands and file operations, also store:

cwd, command, exit_code

stdout/stderr as artifact references (path + sha256 + size)

for file edits: {path, before_sha256, after_sha256, patch_ref}


Generate the current conversation and verification_attempts from events:

Change update_log() from “overwrite with latest lists” to:

read events

derive a compact conversation for the UI

derive verification_attempts for the UI

keep streaming_transcript if you want, but it becomes optional (events are the source of truth)

This preserves your existing JSON file contract while giving you replayability.

