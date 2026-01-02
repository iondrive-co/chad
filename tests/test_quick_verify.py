from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path


def test_build_pytest_command():
    from chad.quick_verify import build_pytest_command

    root = Path("/tmp/project")
    cmd = build_pytest_command(root, workers="3", keyword="mcp", tests=["tests/test_one.py"])

    assert "-m" in cmd and "pytest" in cmd
    assert cmd[cmd.index("-n") + 1] == "3"
    assert cmd[cmd.index("-k") + 1] == "mcp"
    assert cmd[-1] == "tests/test_one.py"


def test_run_quick_pytest_sets_env(monkeypatch, tmp_path):
    import chad.quick_verify as quick_verify

    called = {}

    def fake_run(cmd, cwd=None, env=None):  # noqa: ANN001
        called["cmd"] = cmd
        called["cwd"] = cwd
        called["env"] = env
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(quick_verify.subprocess, "run", fake_run)

    exit_code = quick_verify.run_quick_pytest(tmp_path, "auto", None, ["tests"])

    assert exit_code == 0
    assert called["cwd"] == str(tmp_path)
    assert called["env"]["CHAD_PROJECT_ROOT"] == str(tmp_path)
    assert called["env"]["PYTHONPATH"] == str(tmp_path / "src")
