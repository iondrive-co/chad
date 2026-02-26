"""Verification tools for running linting and tests."""

import subprocess
import sys
from pathlib import Path
from typing import Optional, Dict, Any


def find_python_executable(project_root: Optional[Path] = None) -> str:
    """Find the appropriate Python executable for running commands.

    Args:
        project_root: Project root directory (defaults to cwd)

    Returns:
        Path to Python executable
    """
    if project_root is None:
        project_root = Path.cwd()

    # Check for virtual environment in standard locations
    for venv_dir in [".venv", "venv", ".virtualenv", "env"]:
        venv_path = project_root / venv_dir
        if venv_path.exists():
            if sys.platform == "win32":
                python_exe = venv_path / "Scripts" / "python.exe"
            else:
                python_exe = venv_path / "bin" / "python"

            if python_exe.exists():
                return str(python_exe)

    # Fall back to current Python
    return sys.executable


def verify(
    project_root: Optional[Path] = None,
    lint_only: bool = False,
    visual_only: bool = False,
) -> Dict[str, Any]:
    """Run verification (linting and/or tests).

    Args:
        project_root: Project root directory
        lint_only: Only run linting
        visual_only: Only run visual tests (not supported)

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
    results = {"success": True, "lint": None, "test": None}

    # Run flake8
    if not visual_only:
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

    # Run tests if not lint_only
    if not lint_only:
        try:
            # Skip visual tests
            test_cmd = [python_exe, "-m", "pytest", "tests/", "-v", "-m", "not visual"]
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
