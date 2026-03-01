# Chad: YOLO AI

Coding agents need hand holding to implement complex features, but no one holds Chad's hand. 

Add one or more OpenAI Codex, Claude Code, Google Gemini, Alibaba Qwen, Mistral Vibe, Moonshot Kimi, or OpenCode coding 
agents, decide what happens when you reach a limit (wait for the reset and continue, switch provider), ask for a coding 
task, and Chad will ralph loop to deliver a one-shot result.

<p style="text-align: center;">
  <img src="docs/Chad.png" alt="Chad Code" width="80">
</p>

**The First Warning:** Chad was developed with...  Chad. Yes, this material writes itself. No, high quality robust code 
this is not. 

**World Warning II:** Chad is a risk-taker who knows no limits. Chad runs agents in YOLO mode and has access to 
everything on your hard drive and your internet connection. Especially if you are going to allow remote connections,
consider using a cheap isolated cloud server, the [Weft](https://github.com/iondrive-co/weft) project makes this easy.

### Blah blah how do I run it?
```bash
pip install chad-ai
chad 
```

### How is this better than $Grug?

Chad provides a CLI UI to switch between coding agents (tokens encrypted with a master password you create and
provide for each session), monitors usage quotas, switches between providers, is able to send messages to slack,
and runs multiple tasks in parallel with result merging from their worktrees. It can be run in tunnel mode and 
connected to from a remote ui using a cloudflare tunnel.
<details open>
<summary><b>Screenshots</b></summary>

#### Select coding and verification agents for a task
<img src="docs/screenshot-task-input.png" width="800" alt="Task input panel">

#### Monitor provider accounts with usage tracking
<img src="docs/screenshot-providers.png" width="800" alt="Providers tab with usage">

#### Configure rules to switch providers or wait for usage resets
<img src="docs/screenshot-settings.png" width="800" alt="Action rules configuration">

</details>

### Is this satire? What are you even doing here?

¯\_(ツ)_/¯
