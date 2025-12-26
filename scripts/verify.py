#!/usr/bin/env python3
"""Unified verification script for Chad.

This script runs all verification checks in order:
1. Linting (flake8)
2. Unit tests (fast, no UI)
3. Integration tests (UI with Playwright)

Exit codes:
  0 - All checks passed
  1 - Linting failed
  2 - Unit tests failed
  3 - Integration tests failed

Usage:
  python scripts/verify.py           # Run all checks
  python scripts/verify.py --lint    # Lint only
  python scripts/verify.py --unit    # Unit tests only
  python scripts/verify.py --ui      # UI integration tests only
  python scripts/verify.py --quick   # Lint + unit tests (no UI)
  python scripts/verify.py --file tests/test_web_ui.py  # Specific test file
  python scripts/verify.py --match "test_cancel"  # Tests matching pattern
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
TESTS_DIR = PROJECT_ROOT / "tests"


def run_command(cmd: list[str], description: str, cwd: Path = PROJECT_ROOT) -> bool:
    """Run a command and return True if successful."""
    print(f"\n{'='*60}")
    print(f"  {description}")
    print(f"{'='*60}")
    print(f"$ {' '.join(cmd)}\n")

    result = subprocess.run(cmd, cwd=cwd)
    success = result.returncode == 0

    if success:
        print(f"\n[PASS] {description}")
    else:
        print(f"\n[FAIL] {description} (exit code {result.returncode})")

    return success


def run_lint() -> bool:
    """Run flake8 linting."""
    return run_command(
        [sys.executable, "-m", "flake8", "."],
        "Linting (flake8)"
    )


def run_unit_tests(test_file: str | None = None, match: str | None = None) -> bool:
    """Run unit tests (excluding UI integration tests)."""
    cmd = [
        sys.executable, "-m", "pytest",
        "-v", "--tb=short",
        "--ignore=tests/test_ui_integration.py"
    ]

    if test_file:
        cmd.append(test_file)
    elif match:
        cmd.extend(["-k", match])

    env_prefix = f"PYTHONPATH={SRC_DIR}"
    full_cmd = ["env", env_prefix] + cmd

    return run_command(
        full_cmd,
        f"Unit Tests{f' ({test_file})' if test_file else ''}{f' (matching {match})' if match else ''}"
    )


def run_ui_tests(test_file: str | None = None, match: str | None = None) -> bool:
    """Run UI integration tests with Playwright."""
    cmd = [
        sys.executable, "-m", "pytest",
        "-v", "--tb=short",
        "tests/test_ui_integration.py"
    ]

    if match:
        cmd.extend(["-k", match])

    env_prefix = f"PYTHONPATH={SRC_DIR}"
    full_cmd = ["env", env_prefix] + cmd

    return run_command(
        full_cmd,
        f"UI Integration Tests{f' (matching {match})' if match else ''}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Chad verification checks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--lint", action="store_true", help="Run linting only")
    parser.add_argument("--unit", action="store_true", help="Run unit tests only")
    parser.add_argument("--ui", action="store_true", help="Run UI integration tests only")
    parser.add_argument("--quick", action="store_true", help="Run lint + unit tests (no UI)")
    parser.add_argument("--file", type=str, help="Run specific test file")
    parser.add_argument("--match", "-k", type=str, help="Run tests matching pattern")
    parser.add_argument("--all", action="store_true", help="Run all checks (default)")

    args = parser.parse_args()

    # Determine what to run
    run_lint_check = args.lint or args.quick or args.all or not any([args.lint, args.unit, args.ui, args.quick])
    run_unit_check = args.unit or args.quick or args.all or not any([args.lint, args.unit, args.ui, args.quick])
    run_ui_check = args.ui or args.all or (not any([args.lint, args.unit, args.ui, args.quick]) and not args.file)

    # Override if specific file given
    if args.file:
        if "ui_integration" in args.file:
            run_lint_check = False
            run_unit_check = False
            run_ui_check = True
        else:
            run_lint_check = False
            run_unit_check = True
            run_ui_check = False

    results = []

    # 1. Lint
    if run_lint_check:
        if not run_lint():
            return 1
        results.append("Lint")

    # 2. Unit tests
    if run_unit_check:
        if not run_unit_tests(args.file if args.file and "ui_integration" not in args.file else None, args.match):
            return 2
        results.append("Unit Tests")

    # 3. UI integration tests
    if run_ui_check:
        if not run_ui_tests(match=args.match if not args.file else None):
            return 3
        results.append("UI Integration Tests")

    print(f"\n{'='*60}")
    print(f"  ALL CHECKS PASSED: {', '.join(results)}")
    print(f"{'='*60}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
