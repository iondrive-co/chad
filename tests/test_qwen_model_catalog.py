"""Test cases for Qwen model catalog functionality."""

import os
from pathlib import Path

from chad.util.model_catalog import ModelCatalog


def test_qwen_fallback_models_do_not_include_unsupported_model(monkeypatch, tmp_path):
    """Test that qwen3-coder-plus is not included in Qwen fallback models."""
    monkeypatch.setenv("CHAD_TEMP_HOME", str(tmp_path))
    if os.name == "nt":
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    catalog = ModelCatalog(home_dir=tmp_path)
    models = catalog.get_models("qwen", "test-account")

    # Verify that the unsupported model is not in the fallback models
    assert "qwen3-coder-plus" not in models, (
        "qwen3-coder-plus should not be in the fallback models as it's not supported by Qwen CLI"
    )
    
    # Verify that supported models are still present
    assert "qwen3-coder" in models
    assert "default" in models


def test_qwen_provider_specific_models(monkeypatch, tmp_path):
    """Test that Qwen provider returns appropriate models."""
    monkeypatch.setenv("CHAD_TEMP_HOME", str(tmp_path))
    if os.name == "nt":
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    catalog = ModelCatalog(home_dir=tmp_path)
    
    # Get fallback models specifically
    fallback_models = catalog._fallback("qwen")
    
    # Verify the exact fallback models
    expected_fallbacks = {"qwen3-coder", "default"}
    actual_fallbacks = set(fallback_models)
    
    assert actual_fallbacks == expected_fallbacks, (
        f"Expected fallback models {expected_fallbacks}, got {actual_fallbacks}"
    )
    
    # Verify that unsupported model is not in fallback
    assert "qwen3-coder-plus" not in actual_fallbacks
