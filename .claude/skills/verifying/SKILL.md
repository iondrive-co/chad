---
name: verifying
description: Runs linting and tests to verify code changes have no regressions. Triggers on "verify", "run tests", "check lint", "ensure tests pass", or before completing a task.
allowed-tools: Bash, Read
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
from chad.ui.gradio.verification.tools import verify
result = verify()
print('✓ Verification passed' if result['success'] else f'✗ Failed at {result.get(\"failed_phase\", \"unknown\")}')
exit(0 if result['success'] else 1)
"
```

## Fallback: Manual commands with intelligent Python detection

If the verify() function isn't available, use these commands with fallback logic:

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

# Run verification commands
$PYTHON -m flake8 src/chad
$PYTHON -m pytest tests/ -v --tb=short -n auto
```

**Success**: Both commands exit 0
**Failure**: Fix all issues before marking work complete

Never skip tests with `@pytest.mark.skip`.
