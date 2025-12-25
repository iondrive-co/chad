"""Tests for model discovery helper."""

from pathlib import Path

import pytest

from chad import model_catalog
from chad.model_catalog import ModelCatalog


@pytest.mark.skipif(model_catalog.tomllib is None, reason="TOML parser not available")
def test_codex_models_from_config_and_sessions(tmp_path: Path):
    codex_dir = tmp_path / ".codex"
    (codex_dir / "sessions" / "2025" / "01" / "01").mkdir(parents=True, exist_ok=True)

    config = codex_dir / "config.toml"
    config.write_text(
        'model = "gpt-5.2-codex"\n'
        '[notice.model_migrations]\n'
        '"gpt-old" = "gpt-new"\n'
    )

    session = codex_dir / "sessions" / "2025" / "01" / "01" / "session.jsonl"
    session.write_text(
        '{"model": "gpt-5.1-codex-max"}\n'
        '{"payload": {"model": "gpt-4.1"}}\n'
    )

    catalog = ModelCatalog(home_dir=tmp_path, cache_ttl=0)
    models = catalog.get_models("openai")

    assert "gpt-5.2-codex" in models
    assert "gpt-5.1-codex-max" in models
    assert "gpt-4.1" in models
    assert "gpt-old" in models
    assert "gpt-new" in models
    assert "default" in models
