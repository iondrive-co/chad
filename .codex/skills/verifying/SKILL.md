---
name: verifying
description: Runs flake8 linting and pytest to verify no regressions. Use before completing tasks or when asked to verify/test.
metadata:
  short-description: Run lint + tests
---

# Verifying Changes

## Recommended: Use Python verification function

```bash
# Detect Python executable (prefers project venv, falls back to system)
if [ -f ./.venv/bin/python ]; then
    PYTHON=./.venv/bin/python
elif [ -f ./.venv/Scripts/python.exe ]; then
    PYTHON=./.venv/Scripts/python.exe
elif [ -f ./venv/bin/python ]; then
    PYTHON=./venv/bin/python
elif [ -f ./venv/Scripts/python.exe ]; then
    PYTHON=./venv/Scripts/python.exe
else
    PYTHON=python3
fi

$PYTHON -c "
from chad.util.verification.tools import verify
result = verify()
print('✓ Verification passed' if result['success'] else '✗ Verification failed')
exit(0 if result['success'] else 1)
"
```

## Fallback: Manual commands

```bash
# Detect Python executable (prefers project venv, falls back to system)
if [ -f ./.venv/bin/python ]; then
    PYTHON=./.venv/bin/python
elif [ -f ./.venv/Scripts/python.exe ]; then
    PYTHON=./.venv/Scripts/python.exe
elif [ -f ./venv/bin/python ]; then
    PYTHON=./venv/bin/python
elif [ -f ./venv/Scripts/python.exe ]; then
    PYTHON=./venv/Scripts/python.exe
else
    PYTHON=python3
fi

# Run lint
$PYTHON -m flake8 src/chad

# Run tests
$PYTHON -m pytest tests/ -v --tb=short
```

**Success**: Both exit 0
**Failure**: Fix all issues before completing

Never use `@pytest.mark.skip`.
