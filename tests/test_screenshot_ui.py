import webbrowser
from pathlib import Path

import scripts.screenshot_ui as screenshot_ui


def test_maybe_open_paths_default_skips_open(monkeypatch):
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda uri: opened.append(uri))
    paths = [Path("/tmp/foo.png"), Path("/tmp/bar.png")]

    screenshot_ui.maybe_open_paths(paths, open_images=False)

    assert opened == []


def test_maybe_open_paths_opt_in(monkeypatch):
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda uri: opened.append(uri))
    paths = [Path("/tmp/foo.png"), Path("/tmp/bar.png")]

    screenshot_ui.maybe_open_paths(paths, open_images=True)

    assert opened == [p.as_uri() for p in paths]
