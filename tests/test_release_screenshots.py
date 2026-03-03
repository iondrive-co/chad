"""Tests for release screenshot generation script.

These tests mock Playwright/server interactions to ensure the script configures
screenshots correctly without launching a browser or server.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from scripts import release_screenshots as rs


def test_release_screenshots_use_light_mode(monkeypatch, tmp_path):
    calls: list[str | None] = []

    env = SimpleNamespace(cleaned=False)

    def fake_cleanup():
        env.cleaned = True

    env.cleanup = fake_cleanup

    instance = SimpleNamespace(port=5555)
    started = {
        "create_args": [],
        "start_env": None,
        "stop_instance": None,
    }

    def fake_create_temp_env(*, screenshot_mode=False):
        started["create_args"].append(screenshot_mode)
        return env

    def fake_start_chad(passed_env):
        started["start_env"] = passed_env
        return instance

    def fake_stop_chad(passed_instance):
        started["stop_instance"] = passed_instance

    class DummyPage:
        def evaluate(self, *_args, **_kwargs):
            return None

        def wait_for_timeout(self, *_args, **_kwargs):
            return None

    @contextmanager
    def fake_open_playwright_page(
        port: int,
        *,
        tab=None,
        headless=True,
        viewport=None,
        color_scheme=None,
        render_delay=None,
    ):
        calls.append(color_scheme)
        yield DummyPage()

    def fake_screenshot_page(_page, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"img")
        return output_path

    def fake_screenshot_element(_page, _selector, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"img")
        return output_path

    monkeypatch.setattr(rs, "DOCS_DIR", tmp_path)
    monkeypatch.setattr(rs, "create_temp_env", fake_create_temp_env)
    monkeypatch.setattr(rs, "start_chad", fake_start_chad)
    monkeypatch.setattr(rs, "stop_chad", fake_stop_chad)
    monkeypatch.setattr(rs, "open_playwright_page", fake_open_playwright_page)
    monkeypatch.setattr(rs, "screenshot_page", fake_screenshot_page)
    monkeypatch.setattr(rs, "screenshot_element", fake_screenshot_element)
    monkeypatch.setattr(rs, "inject_followup_visible", lambda _page: None)
    monkeypatch.setattr(rs, "fill_task_form", lambda _page: None)

    rs.main()

    assert started["create_args"] == [True]
    assert started["start_env"] is env
    assert started["stop_instance"] is instance
    assert env.cleaned is True
    assert calls == ["light", "light", "light"]
