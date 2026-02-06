"""Chad verification and screenshot tools.

These functions can be called directly from agent code:
    from chad.ui.gradio.verification.tools import verify, screenshot
    verify()  # Run lint + tests
    screenshot(tab="run")  # Capture UI
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict

from chad.util.config import resolve_project_root
from chad.ui.gradio.verification.visual_test_map import tests_for_paths
from chad.ui.gradio.verification.ui_playwright_runner import (
    ensure_playwright_browsers,
    run_screenshot_subprocess,
)


def _failure(message: str) -> Dict[str, object]:
    return {"success": False, "error": message}


def _get_verify_pytest_timeout() -> int:
    """Return timeout for the full pytest phase in verify()."""
    raw = os.environ.get("CHAD_VERIFY_PYTEST_TIMEOUT")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    # Full suite (including visual tests) commonly exceeds two minutes.
    return 1800


def _parse_pytest_summary(output: str) -> tuple[int, int]:
    """Extract passed/failed counts from pytest output."""
    import re

    passed = failed = 0
    output_lines = output.split("\n")
    for line in reversed(output_lines):
        if re.search(r"=+.*=+", line) and ("passed" in line or "failed" in line):
            passed_match = re.search(r"(\d+) passed", line)
            if passed_match:
                passed = int(passed_match.group(1))
            failed_match = re.search(r"(\d+) failed", line)
            if failed_match:
                failed = int(failed_match.group(1))
            break
    return passed, failed


def _collect_changed_paths(root: Path, env: dict[str, str]) -> list[str]:
    """Collect changed file paths from git (staged + unstaged)."""
    changed: set[str] = set()
    for cmd in (["git", "diff", "--name-only"], ["git", "diff", "--name-only", "--cached"]):
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(root),
            env=env,
            timeout=10,
        )
        if result.returncode == 0:
            changed.update(line.strip() for line in result.stdout.splitlines() if line.strip())
    return sorted(changed)


def verify(lint_only: bool = False, visual_only: bool = False, project_root: str | None = None) -> Dict[str, object]:
    """Run linting and tests to verify no regressions.

    Call this function to:
    - Verify changes haven't broken anything
    - Check code quality before completing work
    - Run only linting by setting lint_only=True when you just need flake8
    - Run only visual tests by setting visual_only=True

    Default strategy:
    - Run all non-visual tests
    - Run only visual tests mapped to changed files
    """
    try:
        if project_root:
            resolved_root = Path(project_root).expanduser()
            if not resolved_root.exists() or not resolved_root.is_dir():
                return _failure(f"Project root does not exist or is not a directory: {resolved_root}")
            pyproject = resolved_root / "pyproject.toml"
            if not pyproject.exists():
                return _failure(f"Project root missing pyproject.toml: {resolved_root}")
            root = resolved_root
            root_reason = "param:project_root"
        else:
            root, root_reason = resolve_project_root()

        env = {
            **os.environ,
            "PYTHONPATH": str(root / "src"),
            "CHAD_PROJECT_ROOT": str(root),
            "CHAD_PROJECT_ROOT_REASON": root_reason,
        }
        env.setdefault("PIP_NO_INDEX", "1")
        env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
        env.setdefault("PIP_PREFER_BINARY", "1")

        # Prefer the project's virtualenv to avoid polluting system Python
        venv_candidates = [
            root / ".venv" / "bin" / "python",
            root / ".venv" / "Scripts" / "python.exe",  # Windows
            root / "venv" / "bin" / "python",
            root / "venv" / "Scripts" / "python.exe",  # Windows
        ]
        python_exec = next((str(path) for path in venv_candidates if path.exists()), sys.executable)

        preflight_note = f"Using project root: {root} (source={root_reason})"
        results: Dict[str, object] = {
            "phases": {},
            "project_root": str(root),
            "project_root_reason": root_reason,
            "preflight": preflight_note,
        }

        # Phase 1: Lint
        lint_result = subprocess.run(
            [python_exec, "-m", "flake8", ".", "--max-line-length=120"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(root),
            timeout=30,
        )
        lint_issues = [line for line in lint_result.stdout.split("\n") if line.strip()]
        results["phases"]["lint"] = {
            "success": lint_result.returncode == 0,
            "issue_count": len(lint_issues),
            "issues": lint_issues[:20],
        }

        message_prefix = f"{preflight_note}. "

        if lint_result.returncode != 0 or lint_only:
            results["success"] = lint_result.returncode == 0
            results["failed_phase"] = "lint" if lint_result.returncode != 0 else None
            results["message"] = (
                f"{message_prefix}Lint failed with {len(lint_issues)} issues"
                if lint_result.returncode != 0
                else f"{message_prefix}Lint-only run completed"
            )
            return results

        # Phase 2: pip check (advisory only - don't block on dependency conflicts)
        pip_check = subprocess.run(
            [python_exec, "-m", "pip", "check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(root),
            env=env,
            timeout=60,
        )
        pip_issues = pip_check.stdout.strip().splitlines()[:20]
        results["phases"]["pip_check"] = {
            "success": pip_check.returncode == 0,
            "issues": pip_issues,
            "blocking": False,  # Advisory only
        }
        if pip_check.returncode != 0:
            # Record as warning but don't block - dependency conflicts are often environmental
            results["warnings"] = results.get("warnings", [])
            results["warnings"].append(
                f"Dependency issues (non-blocking): {'; '.join(pip_issues[:3])}"
            )
            # Continue to tests instead of returning early

        # Phase 3: Tests
        # Check if pytest-xdist is available for parallel execution
        pytest_args = [python_exec, "-m", "pytest", "-v", "--tb=short"]
        try:
            # Test if -n auto is supported
            check_result = subprocess.run(
                [python_exec, "-c", "import pytest_xdist"],
                capture_output=True,
                cwd=str(root),
                env=env,
                timeout=10,
            )
            if check_result.returncode == 0:
                pytest_args.extend(["-n", "auto"])
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
            pass  # Continue without parallel execution

        changed_paths = _collect_changed_paths(root, env)
        visual_expr = " or ".join(tests_for_paths(changed_paths))
        run_visual = visual_only or bool(visual_expr)

        if run_visual:
            try:
                ensure_playwright_browsers()
            except Exception as exc:
                results["phases"]["tests"] = {
                    "success": False,
                    "passed": 0,
                    "failed": 0,
                    "output": f"Playwright setup failed: {exc}",
                }
                results["success"] = False
                results["failed_phase"] = "tests"
                results["message"] = f"{message_prefix}Playwright setup failed: {exc}"
                return results

        passed = failed = 0
        combined_chunks: list[str] = []

        if not visual_only:
            non_visual_result = subprocess.run(
                [*pytest_args, "-m", "not visual"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(root),
                env=env,
                timeout=_get_verify_pytest_timeout(),
            )
            non_visual_output = (non_visual_result.stdout or "")
            if non_visual_result.stderr:
                non_visual_output = (
                    f"{non_visual_output}\n{non_visual_result.stderr}" if non_visual_output else non_visual_result.stderr
                )
            combined_chunks.append(non_visual_output)
            p, f = _parse_pytest_summary(non_visual_result.stdout or "")
            passed += p
            failed += f
            if non_visual_result.returncode != 0:
                results["phases"]["tests"] = {
                    "success": False,
                    "passed": passed,
                    "failed": failed,
                    "output": non_visual_output[-6000:] if len(non_visual_output) > 6000 else non_visual_output,
                }
                results["success"] = False
                results["failed_phase"] = "tests"
                if failed == 0 and passed == 0 and non_visual_output.strip():
                    snippet_lines = [line for line in non_visual_output.splitlines() if line.strip()]
                    snippet = "\n".join(snippet_lines[-5:])
                    results["message"] = f"{message_prefix}Tests failed to run:\n{snippet}"
                else:
                    results["message"] = f"{message_prefix}Tests failed: {failed} failed, {passed} passed"
                return results

        if run_visual:
            visual_args = [*pytest_args, "tests/test_ui_integration.py", "tests/test_ui_playwright_runner.py", "-m", "visual"]
            if visual_expr and not visual_only:
                visual_args.extend(["-k", visual_expr])

            visual_result = subprocess.run(
                visual_args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(root),
                env=env,
                timeout=_get_verify_pytest_timeout(),
            )
            visual_output = (visual_result.stdout or "")
            if visual_result.stderr:
                visual_output = f"{visual_output}\n{visual_result.stderr}" if visual_output else visual_result.stderr
            combined_chunks.append(visual_output)
            p, f = _parse_pytest_summary(visual_result.stdout or "")
            passed += p
            failed += f
            if visual_result.returncode != 0:
                results["phases"]["tests"] = {
                    "success": False,
                    "passed": passed,
                    "failed": failed,
                    "output": visual_output[-6000:] if len(visual_output) > 6000 else visual_output,
                }
                results["success"] = False
                results["failed_phase"] = "tests"
                if failed == 0 and passed == 0 and visual_output.strip():
                    snippet_lines = [line for line in visual_output.splitlines() if line.strip()]
                    snippet = "\n".join(snippet_lines[-5:])
                    results["message"] = f"{message_prefix}Tests failed to run:\n{snippet}"
                else:
                    results["message"] = f"{message_prefix}Tests failed: {failed} failed, {passed} passed"
                return results

        combined_output = "\n".join(chunk for chunk in combined_chunks if chunk)
        visual_scope = "changed visual tests" if (run_visual and visual_expr and not visual_only) else (
            "all visual tests" if run_visual else "no visual tests needed"
        )
        results["phases"]["tests"] = {
            "success": True,
            "passed": passed,
            "failed": failed,
            "visual_scope": visual_scope,
            "output": combined_output[-6000:] if len(combined_output) > 6000 else combined_output,
        }

        results["success"] = True
        base_message = f"{message_prefix}All checks passed: lint clean, {passed} tests passed ({visual_scope})"
        if results.get("warnings"):
            results["message"] = f"{base_message} (with warnings)"
        else:
            results["message"] = base_message
        return results

    except subprocess.TimeoutExpired as e:
        return _failure(f"Verification timed out: {e}")
    except Exception as exc:
        return _failure(f"Verification error: {exc}")


# Component name to CSS selector mapping for granular screenshots
COMPONENT_SELECTORS = {
    "project-path": "#run-top-row",
    "agent-communication": "#agent-chatbot",
    "live-view": "#live-stream-box",
    "provider-summary": "#provider-summary-panel",
    "provider-card": ".provider-cards-row .column:has(.provider-card__header-row)",
    "add-provider": "#add-provider-panel",
    "config": "#config-panel",
}


def screenshot(
    tab: str = "run",
    component: str = "",
    label: str = "",
) -> Dict[str, object]:
    """Capture a screenshot of a UI tab or specific component.

    Use this function to:
    - Understand a UI issue before making changes (label="before")
    - Verify changes look correct after making changes (label="after")
    - Capture specific UI components for focused verification

    Args:
        tab: Which tab to screenshot ("run" or "setup")
        component: Optional specific component to capture. Available components:
            Run tab: "project-path", "agent-communication", "live-view"
            Setup tab: "provider-summary", "provider-card", "add-provider", "config"
            Leave empty to capture the entire tab.
        label: Optional label like "before" or "after" for the filename

    Returns:
        Dict with success status and path to the saved screenshot
    """
    try:
        normalized = tab.lower().strip()
        if normalized.startswith(("s", "p", "c")):
            tab_name = "setup"
        else:
            tab_name = "run"

        selector = None
        if component:
            component_key = component.lower().strip().replace("_", "-")
            selector = COMPONENT_SELECTORS.get(component_key)
            if not selector:
                available = ", ".join(COMPONENT_SELECTORS.keys())
                return _failure(f"Unknown component '{component}'. Available: {available}")

        result = run_screenshot_subprocess(
            tab=tab_name,
            headless=True,
            viewport={"width": 1280, "height": 900},
            label=label if label else None,
            selector=selector,
        )

        if result.get("success"):
            screenshots = result.get("screenshots") or [result.get("screenshot")]
            component_info = f" (component: {component})" if component else ""
            return {
                "success": True,
                "tab": tab_name,
                "component": component or "(full tab)",
                "selector": selector or "(none)",
                "label": label or "(none)",
                "screenshot": result.get("screenshot"),
                "screenshots": screenshots,
                "message": f"Screenshots saved{component_info}: {', '.join(screenshots)}",
            }
        return _failure(result.get("stderr") or result.get("stdout") or "Screenshot failed")
    except Exception as exc:
        return _failure(str(exc))
