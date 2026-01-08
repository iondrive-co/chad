---
name: verifying
description: Runs linting and tests to verify code changes have no regressions. Triggers on "verify", "run tests", "check lint", "ensure tests pass", or before completing a task.
allowed-tools: Bash, Read
---

# Verifying Changes

Run from project root:

```bash
python -m flake8 src/chad --max-line-length=120
python -m pytest tests/ -v --tb=short -n auto
```

**Success**: Both commands exit 0
**Failure**: Fix all issues before marking work complete

Never skip tests with `@pytest.mark.skip`.
