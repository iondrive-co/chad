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


def _write_codex_config(home: Path, model: str, migrations: dict[str, str] | None = None) -> None:
    config = home / ".codex" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'model = "{model}"']
    if migrations:
        lines.append("")
        lines.append("[notice.model_migrations]")
        for old, new in migrations.items():
            lines.append(f'"{old}" = "{new}"')
    config.write_text("\n".join(lines))


def test_openai_models_keep_codex_variants_for_chatgpt(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAD_TEMP_HOME", str(tmp_path))
    home = tmp_path / ".chad" / "codex-homes" / "codex-home"
    _write_auth(home / ".codex" / "auth.json", "plus")
    _write_codex_session(home, "gpt-5.1-codex-max")
    _write_codex_session(home, "codex")
    _write_codex_config(home, "gpt-5.1-codex-max", {"codex": "gpt-5.2-codex"})

    catalog = ModelCatalog(home_dir=tmp_path)
    models = catalog.get_models("openai", "codex-home")

    assert "gpt-5.1-codex-max" in models
    assert "gpt-5.2-codex" in models
    assert "codex" not in {model.lower() for model in models}


def test_openai_models_strip_literal_codex_for_team(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAD_TEMP_HOME", str(tmp_path))
    home = tmp_path / ".chad" / "codex-homes" / "codex-work"
    _write_auth(home / ".codex" / "auth.json", "team")
    _write_codex_session(home, "gpt-5.1-codex-max")
    _write_codex_session(home, "codex")
    _write_codex_config(home, "gpt-5.1-codex-max", {"codex": "gpt-5.2-codex"})

    catalog = ModelCatalog(home_dir=tmp_path)
    models = catalog.get_models("openai", "codex-work")

    assert "gpt-5.1-codex-max" in models
    assert "gpt-5.2-codex" in models
    assert "codex" not in {model.lower() for model in models}


def test_openai_models_use_isolated_codex_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAD_TEMP_HOME", str(tmp_path))
    home = tmp_path / ".chad" / "codex-homes" / "codex-home"
    _write_auth(home / ".codex" / "auth.json", "plus")
    _write_codex_session(home, "o3-mini")

    catalog = ModelCatalog(home_dir=tmp_path)
    models = catalog.get_models("openai", "codex-home")

    assert "o3-mini" in models
