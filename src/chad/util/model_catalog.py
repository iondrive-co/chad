"""Model discovery and normalization for provider dropdowns."""

from __future__ import annotations

import base64
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from chad.util.utils import platform_path, safe_home

try:  # Python 3.11+
    import tomllib  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore
    except ModuleNotFoundError:
        tomllib = None  # type: ignore


def _safe_stat_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


@dataclass
class ModelCatalog:
    """Discover and cache available models per provider."""

    security_mgr: object | None = None
    home_dir: Path = field(default_factory=safe_home)
    cache_ttl: float = 300.0
    max_session_files: int = 60

    # Only include "default" in fallback - specific models vary by account type
    # (ChatGPT accounts vs API accounts have different available models)
    # User's actual available models are discovered from config/session files
    OPENAI_FALLBACK: tuple[str, ...] = ("default",)
    ANTHROPIC_FALLBACK: tuple[str, ...] = (
        "claude-sonnet-4-20250514",
        "claude-opus-4-20250514",
        "default",
    )
    GEMINI_FALLBACK: tuple[str, ...] = (
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "default",
    )
    QWEN_FALLBACK: tuple[str, ...] = (
        "qwen3-coder",
        "qwen3-coder-plus",
        "default",
    )
    MISTRAL_FALLBACK: tuple[str, ...] = ("default",)
    MOCK_FALLBACK: tuple[str, ...] = ("default",)

    _cache: dict[str, tuple[float, list[str]]] = field(default_factory=dict, init=False)

    def supported_providers(self) -> set[str]:
        return {"anthropic", "openai", "gemini", "qwen", "mistral", "mock"}

    def get_models(self, provider: str, account_name: str | None = None) -> list[str]:
        """Return discovered models for a provider, cached with TTL."""
        cache_key = f"{provider}:{account_name or ''}"
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and now - cached[0] < self.cache_ttl:
            return cached[1]

        models = set(self._fallback(provider))
        models |= self._stored_model(provider, account_name)

        if provider == "openai":
            models |= self._codex_config_models(account_name)
            models |= self._codex_session_models(account_name)

        models = {str(m).strip() for m in models if m}
        models = {m for m in models if m}

        if provider == "openai":
            models = {m for m in models if m.lower() != "codex"}

        models.add("default")
        resolved = sorted(models, key=lambda m: (m == "default", m))
        self._cache[cache_key] = (now, resolved)
        return resolved

    # Discovery helpers -------------------------------------------------
    def _fallback(self, provider: str) -> Iterable[str]:
        return {
            "anthropic": self.ANTHROPIC_FALLBACK,
            "openai": self.OPENAI_FALLBACK,
            "gemini": self.GEMINI_FALLBACK,
            "qwen": self.QWEN_FALLBACK,
            "mistral": self.MISTRAL_FALLBACK,
            "mock": self.MOCK_FALLBACK,
        }.get(provider, ("default",))

    def _stored_model(self, provider: str, account_name: str | None) -> set[str]:
        if not account_name or not self.security_mgr:
            return set()
        getter = getattr(self.security_mgr, "get_account_model", None)
        if not getter:
            return set()
        try:
            model = getter(account_name)
        except Exception:
            return set()
        return {str(model)} if model else set()

    def _codex_config_models(self, account_name: str | None) -> set[str]:
        if tomllib is None:
            return set()

        if not account_name:
            return set()

        self._sync_codex_home(account_name)
        config_path = self._codex_home(account_name) / ".codex" / "config.toml"
        if not config_path.exists():
            return set()

        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            return set()

        models: set[str] = set()
        model = data.get("model")
        if model:
            models.add(str(model))

        notice = data.get("notice", {})
        if isinstance(notice, dict):
            migrations = notice.get("model_migrations", {})
            if isinstance(migrations, dict):
                for old, new in migrations.items():
                    if old:
                        models.add(str(old))
                    if new:
                        models.add(str(new))

        return models

    def _codex_session_models(self, account_name: str | None) -> set[str]:
        if not account_name:
            return set()

        self._sync_codex_home(account_name)
        sessions_dir = self._codex_home(account_name) / ".codex" / "sessions"
        if not sessions_dir.exists():
            return set()

        files = list(sessions_dir.rglob("*.jsonl"))
        files.sort(key=_safe_stat_mtime, reverse=True)
        models: set[str] = set()

        for path in files[: self.max_session_files]:
            try:
                with path.open(encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        model = self._extract_model(record)
                        if model:
                            models.add(model)
            except OSError:
                continue

        return models

    @staticmethod
    def _extract_model(record: dict) -> str | None:
        direct = record.get("model")
        if direct:
            return str(direct)

        payload = record.get("payload")
        if isinstance(payload, dict):
            payload_model = payload.get("model")
            if payload_model:
                return str(payload_model)

        return None

    def _codex_home(self, account_name: str) -> Path:
        temp_home = os.environ.get("CHAD_TEMP_HOME")
        base = platform_path(temp_home) if temp_home else platform_path(self.home_dir)
        return platform_path(base / ".chad" / "codex-homes" / account_name)

    def _sync_codex_home(self, account_name: str) -> None:
        """Sync real-home Codex data into the isolated home.

        IMPORTANT: This only syncs files that DON'T already exist in the isolated home.
        Once an account has its own auth.json, it should never be overwritten by the
        real home's auth - that would cause multiple accounts to share credentials.
        """
        isolated_home = platform_path(self._codex_home(account_name) / ".codex")
        real_home = platform_path(safe_home(ignore_temp_home=True) / ".codex")
        if not real_home.exists():
            return

        isolated_home.mkdir(parents=True, exist_ok=True)

        def sync_file_if_missing(src: Path, dest: Path) -> None:
            """Only copy if destination doesn't exist - never overwrite existing auth."""
            try:
                if not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
            except OSError:
                return

        def sync_tree_if_missing(src_dir: Path, dest_dir: Path) -> None:
            """Sync tree but never overwrite existing files."""
            for root, _, files in os.walk(src_dir):
                for filename in files:
                    src_path = platform_path(root) / filename
                    rel_path = src_path.relative_to(src_dir)
                    dest_path = dest_dir / rel_path
                    sync_file_if_missing(src_path, dest_path)

        # Only sync auth.json if the isolated home doesn't have one yet
        # This prevents overwriting account-specific credentials
        sync_file_if_missing(real_home / "auth.json", isolated_home / "auth.json")
        sync_file_if_missing(real_home / "config.toml", isolated_home / "config.toml")
        if (real_home / "sessions").exists():
            sync_tree_if_missing(real_home / "sessions", isolated_home / "sessions")

    def _codex_plan_type(self, account_name: str | None) -> str | None:
        if not account_name:
            return None

        auth_file = self._codex_home(account_name) / ".codex" / "auth.json"
        if not auth_file.exists():
            return None

        try:
            data = json.loads(auth_file.read_text(encoding="utf-8"))
            token = data.get("tokens", {}).get("access_token")
            if not token:
                return None

            payload = token.split(".")[1]
            if payload:
                payload += "=" * (-len(payload) % 4)
            decoded = base64.urlsafe_b64decode(payload or "")
            jwt_data = json.loads(decoded)
            auth_info = jwt_data.get("https://api.openai.com/auth", {})
            plan_type = auth_info.get("chatgpt_plan_type")
            return str(plan_type).lower() if plan_type else None
        except Exception:
            return None
