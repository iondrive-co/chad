import base64
import json
import os
from pathlib import Path
from unittest.mock import Mock

from chad.model_catalog import ModelCatalog
from chad.provider_ui import ProviderUIManager


def _write_auth(auth_file: Path) -> None:
    payload = {"https://api.openai.com/auth": {"chatgpt_plan_type": "plus"}}
    encoded_payload = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    token = f"hdr.{encoded_payload}.sig"

    auth_file.parent.mkdir(parents=True, exist_ok=True)
    auth_file.write_text(json.dumps({"tokens": {"access_token": token}}))


def _write_rate_limit_session(session_file: Path) -> None:
    session_file.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "type": "event_msg",
        "timestamp": "2025-01-01T00:00:00Z",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "primary": {"used_percent": 25, "resets_at": 1700000000},
                "secondary": {"used_percent": 10, "resets_at": 1700000000},
            },
        },
    }
    session_file.write_text(json.dumps(record))


def test_codex_usage_syncs_windows_real_home(monkeypatch, tmp_path):
    isolated_home = tmp_path / "isolated"
    real_home = tmp_path / "real-home"

    monkeypatch.setenv("CHAD_TEMP_HOME", str(isolated_home))
    monkeypatch.setattr(os, "name", "nt", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: real_home))

    account_name = "codex-work"
    auth_file = real_home / ".codex" / "auth.json"
    _write_auth(auth_file)

    session_file = real_home / ".codex" / "sessions" / "2025" / "01" / "01" / "session.jsonl"
    _write_rate_limit_session(session_file)

    security_mgr = Mock()
    security_mgr.list_accounts.return_value = {account_name: "openai"}

    provider_ui = ProviderUIManager(security_mgr, "test-password", ModelCatalog(security_mgr))
    usage_text = provider_ui.get_provider_usage(account_name)

    assert "Current Usage" in usage_text
    assert "Usage data unavailable" not in usage_text
