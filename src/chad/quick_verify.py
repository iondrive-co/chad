from __future__ import annotations

"""Lightweight pytest runner for fast local verification.

Usage:
    python -m chad.quick_verify               # run full suite quickly
    python -m chad.quick_verify -k providers  # keyword filter
    python -m chad.quick_verify tests/test_mcp_playwright.py -k lint
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List

DEFAULT_WORKERS = "auto"


def build_pytest_command(
    project_root: Path, workers: str = DEFAULT_WORKERS, keyword: str | None = None, tests: Iterable[str] | None = None
) -> list[str]:
    """Construct a pytest command for quick runs."""
    cmd: List[str] = [sys.executable, "-m", "pytest", "-q", "-n", workers, "--maxfail", "1"]
    if keyword:
        cmd += ["-k", keyword]
    cmd += list(tests or ["tests"])
    return cmd


def run_quick_pytest(project_root: Path, workers: str, keyword: str | None, tests: Iterable[str]) -> int:
    """Execute pytest with minimal flags and proper environment."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root / "src")
    env["CHAD_PROJECT_ROOT"] = str(project_root)

    cmd = build_pytest_command(project_root, workers, keyword, tests)
    print(f"Running: {' '.join(cmd)} (cwd={project_root})")
    result = subprocess.run(cmd, cwd=str(project_root), env=env)
    return result.returncode


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a fast pytest pass from the current worktree.")
    parser.add_argument("tests", nargs="*", help="Optional test paths or node ids", default=["tests"])
    parser.add_argument("-k", "--keyword", help="Pytest -k expression for filtering")
    parser.add_argument("-n", "--workers", default=DEFAULT_WORKERS, help="Pytest-xdist workers (default: auto)")
    parser.add_argument("--project-root", help="Override project root; sets CHAD_PROJECT_ROOT/PYTHONPATH")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    project_root = (
        Path(args.project_root).expanduser().resolve()
        if args.project_root
        else Path(__file__).resolve().parents[1]
    )
    exit_code = run_quick_pytest(project_root, args.workers, args.keyword, args.tests)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
