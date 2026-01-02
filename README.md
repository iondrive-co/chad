# Chad: YOLO AI

Coding agents need hand holding to implement complex features, but no one holds Chad's hand. 

Add one or more Codex, Claude Code, Gemini, or Mistral Vibe coding agent sessions, ask for a coding task, and Chad will 
verify and reprompt to deliver a one-shot result.

<p style="text-align: center;">
  <img src="docs/Chad.png" alt="Chad Code" width="80">
</p>

**The First Warning:** Chad was developed with...  Chad. Yes, this material writes itself. No, high quality robust code 
this is not. 

**World Warning II:** Chad is a risk-taker who knows no limits. Chad has access to everything on your hard drive and 
your internet connection. Responsible models will stop Chad if they have the right context to do so, and I added a 
safety prompt which will try to give them that context. However, if Chad REALLY wants to complete your task there
are only so many ways I can explicitly prohibit it from ruining your life.

### Blah blah how do I run it?
```bash
pip install chad-ai
chad 
```

### How is this better than $Grug?

-> Gradio UI to manage multiple coding agents, monitor usage quotas, run multiple tasks in parallel and merge results:
<details open>
<summary><b>Screenshots</b></summary>

#### Select coding and verification agents for a task
<img src="docs/screenshot-task-input.png" width="800" alt="Task input panel">

#### Monitor multiple provider accounts with usage tracking
<img src="docs/screenshot-providers.png" width="800" alt="Providers tab">

#### View task details
<img src="docs/screenshot-conversation.png" width="800" alt="Completed task conversation">

#### Run multiple tasks in parallel
<img src="docs/screenshot-task-tabs.png" width="800" alt="Multiple task tabs">

#### Resolve merge conflicts
<img src="docs/screenshot-merge-conflicts.png" width="800" alt="Merge conflict resolution">

</details>

-> Provider tokens encrypted with a master password you create each session

### Is this satire? What are you even doing here?

¯\_(ツ)_/¯

## Developer shortcuts

- MCP tools honor `CHAD_PROJECT_ROOT`; set it (or run `python -m chad.mcp_config`) to point verification at the active worktree. `verify()` now reports the project root before running.
- Quick tests: `python -m chad.quick_verify -k expr tests/test_web_ui.py` (or `scripts/quick-verify.py`) for a fast pytest pass with the right environment.
- Sync helper: `python -m chad.sync_worktree --source <worktree> --dest <repo> [--delete] [--dry-run]` to mirror changes between a worktree and the MCP repo path.
- Noise control: set `CHAD_HIDE_THINKING=1` to hide verbose “Thinking” traces in provider output.
