---
name: verifying
description: Runs flake8 linting and pytest to verify no regressions. Use before completing tasks or when asked to verify/test.
metadata:
  short-description: Run lint + tests
---

# Verifying Changes

```bash
python -m flake8 src/chad --max-line-length=120
python -m pytest tests/ -v --tb=short -n auto
```

**Success**: Both exit 0
**Failure**: Fix all issues before completing

Never use `@pytest.mark.skip`.
