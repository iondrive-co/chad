- Task queue
- Re-open task
- Load balance based on usage and user preference
- Auto publish to pypi
- Prompts should be read in from the user config to be configurable
- For the verify prompt add these suggestions:
  - Code review by different model
  - Run compilation/build commands
  - Execute tests and check results
  - Run linters/formatters
  - Check git status
  - Parse code to verify structure
  - Check for specific functions/classes
  - Analyze dependencies
  - Look up error messages
  - Find documentation
  - Fetch API documentation
  - Read library docs
  - Access language references

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

- When coding AI fails
  - Detect common failure patterns
  - Retry with modified prompts
  - Switch to different model

- Rollback
  - Checkpoint file states
  - Revert failed changes
  - Try alternative approaches


