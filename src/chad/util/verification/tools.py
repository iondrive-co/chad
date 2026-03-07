"""Verification tools for running linting and tests."""

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Dict, Any, List

# Source module → test file(s) mapping. Built by scanning test imports.
# Keys are module path fragments (e.g. "util.providers"), values are test file names.
_MODULE_TO_TESTS: Dict[str, List[str]] = {
    "server.main": [
        "test_api_server.py", "test_end_to_end.py",
        "test_tool_helpers.py", "test_server_multi_client.py"],
    "server.auth": ["test_auth.py", "test_server_multi_client.py"],
    "server.api": ["test_api_server.py"],
    "server.services.task_executor": [
        "test_task_executor.py", "test_unified_streaming.py",
        "test_end_to_end.py", "test_cli_integration.py"],
    "server.services.session_manager": ["test_session_manager.py"],
    "server.services.session_event_loop": [
        "test_session_event_loop.py", "test_verification.py",
        "test_slack_integration.py"],
    "server.services.pty_stream": [
        "test_unified_streaming.py", "test_end_to_end.py",
        "test_auth.py", "test_task_executor.py"],
    "server.services.pty_stream_win": ["test_windows_compat.py"],
    "server.services.event_mux": ["test_unified_streaming.py"],
    "server.services.tunnel_service": ["test_tunnel_service.py"],
    "server.services.slack_service": ["test_slack_integration.py"],
    "server.services.verification": ["test_verification.py"],
    "server.state": [
        "test_api_server.py", "test_end_to_end.py",
        "test_tool_helpers.py", "test_unified_streaming.py"],
    "util.providers": [
        "test_providers.py", "test_session_event_loop.py",
        "test_handoff.py"],
    "util.config_manager": [
        "test_config_manager.py", "test_cleanup.py",
        "test_session_event_loop.py", "test_task_executor.py"],
    "util.config": ["test_config.py"],
    "util.git_worktree": ["test_git_worktree.py", "test_project_setup.py"],
    "util.event_log": [
        "test_unified_streaming.py", "test_message_converter.py",
        "test_handoff.py", "test_api_server.py"],
    "util.prompts": ["test_tools.py"],
    "util.installer": ["test_installer.py", "test_tunnel_service.py",
                       "test_vibe_installation.py"],
    "util.model_catalog": ["test_model_catalog.py",
                           "test_qwen_model_catalog.py"],
    "util.project_setup": ["test_project_setup.py"],
    "util.handoff": ["test_handoff.py", "test_providers.py"],
    "util.utils": ["test_utils.py"],
    "util.cleanup": ["test_cleanup.py"],
    "util.message_converter": ["test_message_converter.py"],
    "util.verification": ["test_tools.py", "test_verification.py"],
    "util.qr": ["test_tunnel_service.py"],
    "ui.cli": ["test_cli_ui.py", "test_cli_screens.py", "test_defaults.py"],
    "ui.cli.app": ["test_cli_ui.py", "test_cli_screens.py"],
    "ui.client": ["test_defaults.py"],
    "ui.client.api_client": ["test_cli_screens.py", "test_defaults.py"],
    "ui.client.stream_client": ["test_cli_ui.py", "test_defaults.py",
                                "test_unified_streaming.py"],
    "ui.client.ws_client": ["test_defaults.py"],
    "ui.terminal_emulator": ["test_terminal_emulator.py",
                             "test_unified_streaming.py"],
    "__main__": ["test_main.py", "test_release_packaging.py",
                 "test_cli_ui.py", "test_defaults.py"],
}


def find_python_executable(project_root: Optional[Path] = None) -> str:
    """Find the appropriate Python executable for running commands."""
    if project_root is None:
        project_root = Path.cwd()

    for venv_dir in [".venv", "venv", ".virtualenv", "env"]:
        venv_path = project_root / venv_dir
        if venv_path.exists():
            if sys.platform == "win32":
                python_exe = venv_path / "Scripts" / "python.exe"
            else:
                python_exe = venv_path / "bin" / "python"

            if python_exe.exists():
                return str(python_exe)

    return sys.executable


def _source_path_to_module(file_path: str) -> Optional[str]:
    """Convert a source file path to a module key for _MODULE_TO_TESTS lookup.

    Examples:
        "src/chad/util/providers.py" → "util.providers"
        "src/chad/server/services/task_executor.py" → "server.services.task_executor"
        "src/chad/__main__.py" → "__main__"
    """
    # Normalize to forward slashes
    p = file_path.replace("\\", "/")
    # Extract the chad-relative path
    match = re.search(r'src/chad/(.+?)\.py$', p)
    if not match:
        return None
    rel = match.group(1).replace("/", ".")
    # Strip __init__ suffix
    rel = re.sub(r'\.__init__$', '', rel)
    return rel


def find_tests_for_files(
    changed_files: List[str],
    project_root: Optional[Path] = None,
) -> List[str]:
    """Map changed source files to relevant test files.

    Args:
        changed_files: List of file paths (relative or absolute).
        project_root: Project root for resolving paths.

    Returns:
        Deduplicated list of test file names (e.g. ["test_providers.py"]).
    """
    tests = set()
    for f in changed_files:
        module = _source_path_to_module(f)
        if module is None:
            continue
        # Try exact match first, then prefix matches
        if module in _MODULE_TO_TESTS:
            tests.update(_MODULE_TO_TESTS[module])
        else:
            for key, test_files in _MODULE_TO_TESTS.items():
                if module.startswith(key) or key.startswith(module):
                    tests.update(test_files)
    return sorted(tests)


def _has_ts_files(changed_files: List[str]) -> bool:
    """Check if any changed files are TypeScript (client/ or ui/src/)."""
    for f in changed_files:
        p = f.replace("\\", "/")
        if re.search(r'(client/src/|ui/src/).*\.(ts|tsx)$', p):
            return True
    return False


def _run_tsc(project_root: Path) -> Dict[str, Any]:
    """Run TypeScript type-checking on ui/ and client/ if npx is available."""
    npx = shutil.which("npx")
    if npx is None:
        return {"passed": True, "output": "npx not found, skipping tsc"}

    errors = []
    for subdir in ["ui", "client"]:
        dir_path = project_root / subdir
        if not (dir_path / "tsconfig.json").exists():
            continue
        result = subprocess.run(
            [npx, "tsc", "--noEmit"],
            cwd=str(dir_path),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            errors.append(f"--- {subdir}/ ---\n{result.stdout}{result.stderr}")

    if errors:
        return {"passed": False, "output": "\n".join(errors)}
    return {"passed": True, "output": "TypeScript check passed"}


def verify(
    project_root: Optional[Path] = None,
    lint_only: bool = False,
    visual_only: bool = False,
    changed_files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run verification (linting and/or tests).

    Args:
        project_root: Project root directory
        lint_only: Only run linting
        visual_only: Only run visual tests (not supported)
        changed_files: If provided, run only tests relevant to these files
            and add TypeScript checking when TS files are included.
            Falls back to full test suite if no mapping is found.

    Returns:
        Dict with verification results
    """
    if project_root is None:
        project_root = Path.cwd()

    if visual_only:
        return {
            "success": False,
            "error": "Visual tests are not supported"
        }

    python_exe = find_python_executable(project_root)
    results: Dict[str, Any] = {
        "success": True, "lint": None, "test": None, "tsc": None,
    }

    # Run flake8
    try:
        lint_cmd = [python_exe, "-m", "flake8", "."]
        result = subprocess.run(
            lint_cmd,
            cwd=project_root,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            results["success"] = False
            results["lint"] = {
                "passed": False,
                "output": result.stdout + result.stderr
            }
        else:
            results["lint"] = {
                "passed": True,
                "output": "Linting passed"
            }
    except Exception as e:
        results["success"] = False
        results["lint"] = {
            "passed": False,
            "output": f"Lint error: {e}"
        }

    # Run TypeScript checking if TS files changed
    if changed_files and _has_ts_files(changed_files):
        tsc_result = _run_tsc(project_root)
        results["tsc"] = tsc_result
        if not tsc_result["passed"]:
            results["success"] = False

    # Run tests if not lint_only
    if not lint_only:
        try:
            test_cmd = [python_exe, "-m", "pytest"]

            # Use targeted tests if changed_files provided
            if changed_files:
                targeted = find_tests_for_files(changed_files, project_root)
                if targeted:
                    test_cmd.extend(
                        [f"tests/{t}" for t in targeted]
                    )
                else:
                    test_cmd.append("tests/")
            else:
                test_cmd.append("tests/")

            test_cmd.extend(["-v", "-m", "not visual"])

            result = subprocess.run(
                test_cmd,
                cwd=project_root,
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                results["success"] = False
                results["test"] = {
                    "passed": False,
                    "output": result.stdout + result.stderr
                }
            else:
                results["test"] = {
                    "passed": True,
                    "output": result.stdout
                }
        except Exception as e:
            results["success"] = False
            results["test"] = {
                "passed": False,
                "output": f"Test error: {e}"
            }

    return results
