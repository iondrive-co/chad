"""Model discovery and normalization for provider dropdowns."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

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
    home_dir: Path = field(default_factory=Path.home)
    cache_ttl: float = 300.0
    max_session_files: int = 60

    OPENAI_FALLBACK: tuple[str, ...] = (
        "gpt-5.2-codex",
        "gpt-5.1-codex-max",
        "gpt-5.1-codex",
        "gpt-5.1-codex-mini",
        "gpt-5.2",
        "gpt-4.1",
        "gpt-4.1-mini",
        "o3",
        "o4-mini",
        "codex-mini",
        "default",
    )
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
    MISTRAL_FALLBACK: tuple[str, ...] = ("default",)

    _cache: dict[str, tuple[float, list[str]]] = field(default_factory=dict, init=False)

    def supported_providers(self) -> set[str]:
        return {"anthropic", "openai", "gemini", "mistral"}

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
            models |= self._codex_config_models()
            models |= self._codex_session_models()

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
            "mistral": self.MISTRAL_FALLBACK,
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

    def _codex_config_models(self) -> set[str]:
        if tomllib is None:
            return set()

        config_path = self.home_dir / ".codex" / "config.toml"
        if not config_path.exists():
            return set()

        try:
            data = tomllib.loads(config_path.read_text())
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

    def _codex_session_models(self) -> set[str]:
        sessions_dir = self.home_dir / ".codex" / "sessions"
        if not sessions_dir.exists():
            return set()

        files = list(sessions_dir.rglob("*.jsonl"))
        files.sort(key=_safe_stat_mtime, reverse=True)
        models: set[str] = set()

        for path in files[: self.max_session_files]:
            try:
                with path.open() as fh:
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
