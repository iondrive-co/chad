- Tasks:
  - Get rid of provider roles, set coding and management when specifying a task
  - Get rid of management AI. Once coding AI is done select a next step: Verify visually, further request (not implemented yet), etc
  - Manually add new task tabs for each new task
- Continue session / further request
- Verify standard approach using establish broken test (ideally visual with MCP browser), fix issue, and retest
- Classes for 1 sentence summaries and splittling tasks, self-contained including prompt, tested first then integration
- Load balance based on usage, user preference, context limits hit, - will need: 
  - task splitting to support session continuation per agent
  - summarize progress and resume from checkpoints
  - independent copies of project on separate git branches
- Rollback
  - Checkpoint file states
  - Revert failed changes
  - Try alternative approaches


