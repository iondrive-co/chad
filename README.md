# Chad: YOLO AI

Coding agents need hand holding to implement complex features, but no one holds Chad's hand. 

Add one or more OpenAI Codex, Claude Code, Google Gemini, Alibaba Qwen, Mistral Vibe, Moonshot Kimi, or OpenCode coding 
agents, decide what happens when you reach a limit (wait for the reset and continue, switch provider), ask for a coding 
task, and Chad will ralph loop to deliver a one-shot result.

**The First Warning:** Chad was developed with...  Chad. Yes, this material writes itself. No, high quality robust code 
this is not. 

**World Warning II:** Chad is a risk-taker who knows no limits. Chad runs agents in YOLO mode and has access to 
everything on your hard drive and your internet connection. Especially if you are going to allow remote connections,
consider using a cheap isolated cloud server, the [Weft](https://github.com/iondrive-co/weft) project makes this easy.

### Blah blah how do I run it?

- Install the latest version from the [releases page](https://github.com/iondrive-co/chad/releases) or with
[`pipx`](https://pipx.pypa.io/stable/) `pipx install chad-ai`
- Run it locally with the `chad` command OR
- Run it remotely with the `chad --tunnel` command and connect at https://iondrive.co/Chad

### How is this better than $Grug?

- Switch between agents (tokens encrypted with a master password you create and provide for each session)
- Optional remote access at no cost
- Display usage from multiple providers
- Await reset or switch providers when a desired hourly or weekly usage level is reached
- Run multiple tasks in parallel with git worktrees
- Send progress messages to slack and notify you once a task is done
- Chat view for continuing sessions or reviewing changes

<details open>
<summary><b>Screenshots</b></summary>

#### Monitor provider accounts with usage tracking
<img src="https://raw.githubusercontent.com/iondrive-co/chad/main/docs/screenshot-providers.png" width="800" alt="Providers tab with usage">

#### Configure rules to switch providers or wait for usage resets
<img src="https://raw.githubusercontent.com/iondrive-co/chad/main/docs/screenshot-settings.png" width="800" alt="Action rules configuration">

#### Run tasks with selected coding and verification agents
<img src="https://raw.githubusercontent.com/iondrive-co/chad/main/docs/screenshot-task-input.png" width="800" alt="Task input panel">
</details>

### Resuming Sessions

Chad only restores previous sessions when you start it with `--resume`. That keeps a normal startup clean; use
`chad --resume` (or `chad --mode server --resume`) when you want recent sessions to reappear in the session list
so you can send a follow-up message and pick up where you left off. You can resume with a different provider than
the one that originally did the work; Chad reconstructs the conversation context from its session logs. If the
session's git worktree is still around, work continues there. Session history is retained according to the
cleanup days setting (default: 3 days).

### Is this satire? What are you even doing here?

<p style="text-align: center;">
  <img src="https://raw.githubusercontent.com/iondrive-co/chad/main/docs/Chad.png" alt="Chad Code" width="80">
</p>
