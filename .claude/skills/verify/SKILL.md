---
name: verify
description: Run linting and tests to verify no regressions. Use when completing a task, checking code quality, or before finishing work. Triggers on "verify", "run tests", "check lint", "ensure tests pass".
allowed-tools: Bash, Read
---

# Verify - Lint and Test Runner

Run this skill after making code changes to ensure no regressions.

## Quick Start

Run full verification:
```bash
cd /home/miles/chad
./venv/bin/python -m flake8 src/chad --max-line-length=120
./venv/bin/python -m pytest tests/ -v --tb=short -n auto
```

## Verification Phases

### Phase 1: Lint Check
```bash
./venv/bin/python -m flake8 src/chad --max-line-length=120
```
- Exit code 0 = lint passes
- Any output indicates lint errors to fix

### Phase 2: Dependency Check
```bash
./venv/bin/python -m pip check
```

### Phase 3: Run All Tests
```bash
./venv/bin/python -m pytest tests/ -v --tb=short -n auto
```
- Uses pytest-xdist for parallel execution
- Runs unit, integration, and visual tests

## Interpreting Results

**Success indicators:**
- Flake8 exits with code 0 and no output
- Pytest shows "X passed" with exit code 0

**Failure indicators:**
- Flake8 outputs file:line:col errors
- Pytest shows "X failed" - fix these before completing

## Important

- Always fix ALL lint and test failures before marking work complete
- Never skip tests with @pytest.mark.skip
- If a test fails, fix the code or the test - do not skip it
