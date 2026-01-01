import base64
import json
from pathlib import Path

from chad.model_catalog import ModelCatalog


def _write_auth(auth_file: Path, plan_type: str) -> None:
    payload = {"https://api.openai.com/auth": {"chatgpt_plan_type": plan_type}}
    encoded_payload = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    token = f"hdr.{encoded_payload}.sig"

    auth_file.parent.mkdir(parents=True, exist_ok=True)
    auth_file.write_text(json.dumps({"tokens": {"access_token": token}}))


def _write_codex_session(home: Path, model: str) -> None:
    session = home / ".codex" / "sessions" / "2025" / "01" / "01" / "session.jsonl"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text(json.dumps({"model": model}))


def test_openai_models_hide_codex_for_chatgpt(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAD_TEMP_HOME", str(tmp_path))
    home = tmp_path / ".chad" / "codex-homes" / "codex-home"
    _write_auth(home / ".codex" / "auth.json", "plus")
    _write_codex_session(home, "gpt-5.1-codex-max")

    catalog = ModelCatalog(home_dir=home)
    models = catalog.get_models("openai", "codex-home")

    assert all("codex" not in model.lower() for model in models)


def test_openai_models_keep_codex_for_team(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAD_TEMP_HOME", str(tmp_path))
    home = tmp_path / ".chad" / "codex-homes" / "codex-work"
    _write_auth(home / ".codex" / "auth.json", "team")
    _write_codex_session(home, "gpt-5.1-codex-max")

    catalog = ModelCatalog(home_dir=home)
    models = catalog.get_models("openai", "codex-work")

    assert "gpt-5.1-codex-max" in models
