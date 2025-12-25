- Continue session
- Verify standard approach using establish broken test (ideally visual with MCP browser), fix issue, and retest
- Load balance based on usage, user preference, context limits hit, - will need: 
  - task splitting to support session continuation per agent
  - summarize progress and resume from checkpoints
  - independent copies of project on separate git branches
- Rollback
  - Checkpoint file states
  - Revert failed changes
  - Try alternative approaches


