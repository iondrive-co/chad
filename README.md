# Chad: YOLO AI

Coding agents need hand holding to implement complex features, but no one holds Chad's hand.

- Installs and logs you in to Codex, Claude Code, Gemini, or Mistral Vibe coding agent sessions
- Chad will break your tasks down in a management session, and prompt the coding agent running in a separate session
- When a coding agent starts equivocating or half-baking Chad will verify and reprompt until the task is complete

**The First Warning:** Chad was developed with...  Chad. Yes, this material writes itself. No, high quality robust code 
this is not. 

**World Warning II:** Chad is a risk-taker who knows no limits. Chad has access to everything on your hard drive and 
your internet connection. Responsible models will stop Chad if they have the right context to do so, and I added a 
safety prompt which will try to give them that context. However, if Chad REALLY wants to complete your task there
are only so many ways I can explicitly prohibit it from ruining your life.

![Task](docs/RunningTask.png)
![Providers](docs/Providers.png)

From Grug to:

- Storing your provider preferences in a config file, encrypted with a master password you create and supply every 
  session, which can be wiped by specifying a new password at login
- Letting you add and remove agents, assign them to roles, change their preferred model, and view their usage

### Blah blah how do I run it?
```bash
pip install chad-ai
chad 
```
(someone likes the sound of their own name)

### Is this satire?

Sadly, no. "As a society, what are we even doing here?" might be a better question.
