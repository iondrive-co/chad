"""MCP tools for Chad.

Three core capabilities:
1. verify() - Run lint + all tests to check for regressions
2. screenshot(tab, component) - Capture UI for understanding and verification
3. hypothesis/check_result/report - Track hypotheses with binary rejection checks

Component Screenshots
--------------------
The screenshot() tool supports capturing specific UI components instead of full tabs.
This is useful for:
- Testing individual component changes without full-page screenshots
- Verifying specific areas of the UI
- Creating focused before/after comparisons

Available components for each tab:

**Run Tab (tab="run"):**
- "project-path" - Project path and agent selection dropdowns
- "agent-communication" - The chat interface for agent messages
- "live-view" - Live activity stream (may be empty initially)

**Providers Tab (tab="providers"):**
- "provider-summary" - Summary panel showing all providers
- "provider-card" - First visible provider card with model/reasoning settings
- "add-provider" - The "Add New Provider" accordion panel

Usage Examples:
    # Full tab screenshot
    screenshot(tab="run")

    # Specific component screenshot
    screenshot(tab="run", component="project-path")
    screenshot(tab="providers", component="provider-card")

    # With label for before/after comparisons
    screenshot(tab="run", component="agent-communication", label="before")
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict

from mcp.server.fastmcp import FastMCP

from .investigation_report import HypothesisTracker
from .ui_playwright_runner import run_screenshot_subprocess
from .mcp_config import ensure_global_mcp_config

SERVER = FastMCP("chad-ui-playwright")


def _project_root() -> Path:
    """Get the project root directory."""
    return Path(__file__).parents[2]


def _failure(message: str) -> Dict[str, object]:
    return {"success": False, "error": message}


# Ensure Codex global config has this MCP server
ensure_global_mcp_config()


# =============================================================================
# Tool 1: VERIFY - Run lint + all tests
# =============================================================================

@SERVER.tool()
def verify() -> Dict[str, object]:
    """Run linting and ALL tests (unit + integration + visual) to verify no regressions.

    Call this tool to:
    - Verify changes haven't broken anything
    - Check code quality before completing work

    Returns results from each phase: lint, unit tests, visual tests.
    """
    try:
        project_root = _project_root()
        env = {**os.environ, "PYTHONPATH": str(project_root / "src")}
        results: Dict[str, object] = {"phases": {}}

        # Phase 1: Lint
        lint_result = subprocess.run(
            [sys.executable, "-m", "flake8", ".", "--max-line-length=120"],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            timeout=60,
        )
        lint_issues = [line for line in lint_result.stdout.split("\n") if line.strip()]
        results["phases"]["lint"] = {
            "success": lint_result.returncode == 0,
            "issue_count": len(lint_issues),
            "issues": lint_issues[:20],  # First 20 issues
        }

        if lint_result.returncode != 0:
            results["success"] = False
            results["failed_phase"] = "lint"
            results["message"] = f"Lint failed with {len(lint_issues)} issues"
            return results

        # Phase 2: All tests (unit + integration + visual)
        test_result = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "--tb=short"],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            env=env,
            timeout=600,  # 10 minutes for all tests including visual
        )

        # Parse test counts
        passed = failed = 0
        for line in test_result.stdout.split("\n"):
            if "passed" in line or "failed" in line:
                import re
                match = re.search(r"(\d+) passed", line)
                if match:
                    passed = int(match.group(1))
                match = re.search(r"(\d+) failed", line)
                if match:
                    failed = int(match.group(1))

        results["phases"]["tests"] = {
            "success": test_result.returncode == 0,
            "passed": passed,
            "failed": failed,
            "output": test_result.stdout[-6000:] if len(test_result.stdout) > 6000 else test_result.stdout,
        }

        if test_result.returncode != 0:
            results["success"] = False
            results["failed_phase"] = "tests"
            results["message"] = f"Tests failed: {failed} failed, {passed} passed"
            return results

        results["success"] = True
        results["message"] = f"All checks passed: lint clean, {passed} tests passed"
        return results

    except subprocess.TimeoutExpired as e:
        return _failure(f"Verification timed out: {e}")
    except Exception as exc:
        return _failure(f"Verification error: {exc}")


# =============================================================================
# Tool 2: SCREENSHOT - Capture UI for verification
# =============================================================================

# Component name to CSS selector mapping for granular screenshots
# Note: Use IDs where available, CSS class selectors as fallback
COMPONENT_SELECTORS = {
    # Run tab components
    "project-path": "#run-top-inputs",
    "agent-communication": "#agent-chatbot",
    "live-view": "#live-stream-box",
    # Providers tab components
    "provider-summary": "#provider-summary-panel",
    "provider-card": ".gr-group:has(.provider-card__header-text)",  # First visible provider card
    "add-provider": "#add-provider-panel",
}


@SERVER.tool()
def screenshot(
    tab: str = "run",
    component: str = "",
    label: str = "",
) -> Dict[str, object]:
    """Capture a screenshot of a UI tab or specific component.

    Use this tool to:
    - Understand a UI issue before making changes (label="before")
    - Verify changes look correct after making changes (label="after")
    - Capture specific UI components for focused verification

    Args:
        tab: Which tab to screenshot ("run" or "providers")
        component: Optional specific component to capture. Available components:
            Run tab: "project-path", "agent-communication", "live-view"
            Providers tab: "provider-summary", "provider-card", "add-provider"
            Leave empty to capture the entire tab.
        label: Optional label like "before" or "after" for the filename

    Returns:
        Path to the saved screenshot

    Examples:
        screenshot(tab="run") - Full run tab
        screenshot(tab="run", component="project-path") - Just the project path panel
        screenshot(tab="providers", component="provider-card") - A single provider card
    """
    normalized = tab.lower().strip()
    tab_name = "providers" if normalized.startswith("p") else "run"

    # Resolve component to selector
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


# =============================================================================
# Tool 3: HYPOTHESIS TRACKING - Record and verify hypotheses
# =============================================================================

# Global tracker for the current session
_current_tracker: HypothesisTracker | None = None


def _get_or_create_tracker(tracker_id: str | None = None) -> HypothesisTracker:
    """Get existing tracker or create new one."""
    global _current_tracker
    if tracker_id:
        return HypothesisTracker(tracker_id)
    if _current_tracker is None:
        _current_tracker = HypothesisTracker()
    return _current_tracker


@SERVER.tool()
def hypothesis(
    description: str,
    checks: str,
    tracker_id: str = "",
) -> Dict[str, object]:
    """Record a hypothesis with binary rejection checks.

    Each check is a condition that, if FALSE, would REJECT this hypothesis.
    All checks must be completed and results filed before work is complete.

    Args:
        description: Your theory about what's causing the issue
        checks: Comma-separated list of binary checks that would reject this hypothesis
                Example: "CSS is being applied,Element exists in DOM,No JS errors"
        tracker_id: Optional - resume an existing tracker

    Returns:
        tracker_id to use for subsequent calls
        hypothesis_id to reference this hypothesis
        List of checks that need to be verified
    """
    try:
        tracker = _get_or_create_tracker(tracker_id if tracker_id else None)
        check_list = [c.strip() for c in checks.split(",") if c.strip()]

        if not check_list:
            return _failure("At least one check is required")

        hypothesis_id = tracker.add_hypothesis(description, check_list)

        return {
            "success": True,
            "tracker_id": tracker.id,
            "hypothesis_id": hypothesis_id,
            "description": description,
            "checks_to_verify": [
                {"index": i, "check": c}
                for i, c in enumerate(check_list)
            ],
            "message": f"Hypothesis #{hypothesis_id} recorded. File results for each check using check_result().",
        }
    except Exception as exc:
        return _failure(f"Failed to record hypothesis: {exc}")


@SERVER.tool()
def check_result(
    tracker_id: str,
    hypothesis_id: int,
    check_index: int,
    passed: bool,
    notes: str = "",
) -> Dict[str, object]:
    """File the result of a binary check.

    Args:
        tracker_id: The tracker ID from hypothesis()
        hypothesis_id: Which hypothesis (1-indexed)
        check_index: Which check (0-indexed)
        passed: True if check passed (hypothesis survives), False if it failed (hypothesis rejected)
        notes: Optional notes about what you found

    Returns:
        Updated status of the hypothesis
        Remaining checks that still need results
    """
    try:
        tracker = HypothesisTracker(tracker_id)
        result = tracker.file_check_result(hypothesis_id, check_index, passed, notes)

        if "error" in result:
            return _failure(result["error"])

        pending = tracker.get_pending_checks()

        return {
            "success": True,
            "hypothesis_id": hypothesis_id,
            "check_index": check_index,
            "passed": passed,
            "hypothesis_status": result["hypothesis_status"],
            "checks_complete": result["checks_complete"],
            "checks_total": result["checks_total"],
            "pending_checks": pending[:5],  # Next 5 pending checks
            "message": (
                f"Check filed. Hypothesis is now {result['hypothesis_status']}."
                if result["hypothesis_status"] != "pending"
                else f"Check filed. {len(pending)} checks remaining."
            ),
        }
    except FileNotFoundError:
        return _failure(f"Tracker {tracker_id} not found")
    except Exception as exc:
        return _failure(f"Failed to file check result: {exc}")


@SERVER.tool()
def report(tracker_id: str, screenshot_before: str = "", screenshot_after: str = "") -> Dict[str, object]:
    """Get the final report to return to the user.

    Call this at the completion of work to get a summary of all hypotheses
    and their check results.

    Args:
        tracker_id: The tracker ID from hypothesis()
        screenshot_before: Optional path to before screenshot
        screenshot_after: Optional path to after screenshot

    Returns:
        Complete report with all hypotheses, checks, and their results
    """
    try:
        tracker = HypothesisTracker(tracker_id)

        if screenshot_before:
            tracker.set_screenshot("before", screenshot_before)
        if screenshot_after:
            tracker.set_screenshot("after", screenshot_after)

        full_report = tracker.get_report()

        # Format for easy reading
        formatted_hypotheses = []
        for h in full_report["hypotheses"]:
            formatted_hypotheses.append(
                f"{h['status_icon']} H{h['id']}: {h['description']}\n" +
                "\n".join(h["checks"])
            )

        return {
            "success": True,
            "tracker_id": tracker_id,
            "summary": full_report["summary"],
            "confirmed": full_report["confirmed_hypotheses"],
            "rejected": full_report["rejected_hypotheses"],
            "all_checks_complete": full_report["all_checks_complete"],
            "incomplete_checks": full_report["incomplete_checks"],
            "screenshots": full_report["screenshots"],
            "formatted_report": "\n\n".join(formatted_hypotheses),
            "file_path": full_report["file_path"],
        }
    except FileNotFoundError:
        return _failure(f"Tracker {tracker_id} not found")
    except Exception as exc:
        return _failure(f"Failed to get report: {exc}")


# =============================================================================
# Bootstrap/Discovery
# =============================================================================

@SERVER.tool()
def list_tools() -> Dict[str, object]:
    """List available MCP tools and their purposes.

    Use this to understand what tools are available.
    """
    return {
        "success": True,
        "tools": {
            "verify": "Run lint + ALL tests (unit + visual) to check for regressions",
            "screenshot": "Capture UI tab or specific component screenshot",
            "hypothesis": "Record a hypothesis with binary rejection checks",
            "check_result": "File the result of a binary check (pass/fail)",
            "report": "Get final report of all hypotheses and results",
        },
        "screenshot_components": {
            "run_tab": ["project-path", "agent-communication", "live-view"],
            "providers_tab": ["provider-summary", "provider-card", "add-provider"],
        },
        "workflow": [
            "1. Use screenshot(tab, component, label='before') if working on UI",
            "2. Use hypothesis() to record theories with checks",
            "3. Use check_result() to file results as you verify each check",
            "4. Use verify() to confirm no regressions",
            "5. Use screenshot(tab, component, label='after') if working on UI",
            "6. Use report() to get final summary for user",
        ],
        "screenshot_examples": [
            "screenshot(tab='run') - Full run tab",
            "screenshot(tab='run', component='project-path') - Project path panel only",
            "screenshot(tab='run', component='agent-communication') - Chat panel only",
            "screenshot(tab='providers', component='provider-card') - Single provider card",
            "screenshot(tab='providers', component='provider-summary') - Summary panel only",
        ],
    }


if __name__ == "__main__":
    SERVER.run()
