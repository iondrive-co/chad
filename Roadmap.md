- Task queue
- Re-open task
- Load balance based on usage and user preference

- Track how well each model is doing
  - Response time
  - Task completion rate
  - Code quality (if verifiable)
  - Number of errors/retries needed

- Management AI decides which model to use
  - Route tasks to best-performing model
  - Switch from i.e. opus to sonnet based on expected usage and available credits, switch providers for the same
  - Switch models if one is struggling
  - Fall back to alternative if primary fails

- Have multiple models work on same task
  - Generate multiple solutions
  - Management AI picks best approach
  - Merge complementary solutions

- Handle tasks that exceed context limits
  - Summarize progress periodically
  - Maintain condensed task history
  - Resume from checkpoints

- Rollback
  - Checkpoint file states
  - Revert failed changes
  - Try alternative approaches


