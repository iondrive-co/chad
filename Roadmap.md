- Tasks:
  - Get rid of provider roles, set coding and management when specifying a task
  - Get rid of management AI. Once coding AI is done select a next step: Verify visually, further request (not implemented yet), etc
  - Manually add new task tabs for each new task
- Continue session / further request
- Verify standard approach:
  - Before implementing describe the problem, then write a test to demonstrate the problem (visually with MCP browser if 
  necessary), then modify your description of the problem based on what the test shows
  - After fixing demonstrate that the test now passes and that no other tests are broken
- Classes for 1 sentence summaries and splitting tasks, self-contained including prompt, tested first then integration
- Load balance based on usage, user preference, context limits hit, etc - will need: 
  - task splitting to support session continuation per agent
  - independent copies of project on separate git branches
- Rollback
  - summarize progress and resume from checkpoints
  - Revert failed changes

