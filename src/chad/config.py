"""Project configuration utilities for Chad."""

from __future__ import annotations

import os
from pathlib import Path


def resolve_project_root(project_root: Path | None = None) -> tuple[Path, str]:
    """Resolve the project root, preferring explicit overrides.

    Returns:
        Tuple of (resolved_path, reason)
    """
    if project_root:
        resolved = Path(project_root).expanduser().resolve()
        return resolved, "argument"

    env_root = os.environ.get("CHAD_PROJECT_ROOT")
    fallback_reason = "module_default"

    if env_root:
        candidate = Path(env_root).expanduser()
        if candidate.exists():
            return candidate.resolve(), "env:CHAD_PROJECT_ROOT"
        fallback_reason = f"env_missing:{candidate}"

    return Path(__file__).resolve().parents[2], fallback_reason


def ensure_project_root_env(project_root: Path | None = None) -> dict[str, object]:
    """Ensure CHAD_PROJECT_ROOT is set for all spawned agents/processes.

    If the variable is already present, it is left unchanged. Otherwise, it is
    set using resolve_project_root(project_root) and CHAD_PROJECT_ROOT_REASON is
    also set to aid debugging.
    """
    existing_env = os.environ.get("CHAD_PROJECT_ROOT")
    if existing_env:
        reason = f"env:{Path(existing_env).expanduser().resolve()}"
        if "CHAD_PROJECT_ROOT_REASON" not in os.environ:
            os.environ["CHAD_PROJECT_ROOT_REASON"] = reason
            return {"project_root": existing_env, "project_root_reason": reason, "changed": True}
        return {
            "project_root": existing_env,
            "project_root_reason": os.environ.get("CHAD_PROJECT_ROOT_REASON", reason),
            "changed": False,
        }

    resolved, reason = resolve_project_root(project_root)
    os.environ["CHAD_PROJECT_ROOT"] = str(resolved)
    os.environ["CHAD_PROJECT_ROOT_REASON"] = reason
    return {"project_root": str(resolved), "project_root_reason": reason, "changed": True}
