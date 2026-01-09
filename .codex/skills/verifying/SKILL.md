---
name: verifying
description: Runs flake8 linting and pytest to verify no regressions. Use before completing tasks or when asked to verify/test.
metadata:
  short-description: Run lint + tests
---

# Verifying Changes

```bash
./venv/bin/python -m flake8 src/chad --max-line-length=120
# Core/unit/integration (non-visual)
./venv/bin/python -m pytest tests/ -v --tb=short -n auto --ignore tests/test_ui_integration.py --ignore tests/test_ui_playwright_runner.py

# Targeted visual tests (only those mapped to files you changed)
VTESTS=$(./venv/bin/python - <<'PY'
import subprocess
from chad.verification.visual_test_map import tests_for_paths
changed = subprocess.check_output(["git", "diff", "--name-only"], text=True).splitlines()
tests = tests_for_paths(changed)
print(" or ".join(tests))
PY
)
if [ -n "$VTESTS" ]; then
  ./venv/bin/python -m pytest tests/test_ui_integration.py tests/test_ui_playwright_runner.py -v --tb=short -k "$VTESTS"
fi
# If you add/change UI, update src/chad/verification/visual_test_map.py so this stays accurate.
```

**Success**: Both exit 0
**Failure**: Fix all issues before completing

Never use `@pytest.mark.skip`.
