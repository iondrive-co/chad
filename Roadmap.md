- Prompt the agent with the verify standard approach described in AGENTS.md. Do handoffs to verification via a json doc on disk
- Classes for 1 sentence summaries and splitting tasks, self-contained including prompt, tested first then integration
- Load balance based on usage, user preference, context limits hit, etc - will need: 
  - task splitting to support session continuation per agent
  - Local projects: copy directory into system temp and work on it
  - Git remote projects: checkout and branch, push branch back to remote for review once done
- Rollback
  - summarize progress and resume from checkpoints
  - Revert failed changes

