"""Tests for the UI autobuild helper."""

import os
import subprocess
import time
from pathlib import Path
from unittest.mock import call, patch

from chad.util.ui_build import _is_stale, ensure_ui_built

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_no_ui_source_file_is_gitignored():
    """Every UI/client source file must be trackable by git.

    A bare ``lib/`` rule in .gitignore (meant for Python build artifacts) once
    also matched ``ui/src/lib/``, so ``transcript.ts`` was silently never
    committed. CI then failed the React build (``Cannot find module
    '../lib/transcript.ts'``) and ``/`` returned 404. Guard against any UI or
    client source file being excluded by gitignore.
    """
    source_files = []
    for base in ("ui/src", "client/src"):
        root = _REPO_ROOT / base
        if not root.is_dir():
            continue
        for pattern in ("*.ts", "*.tsx", "*.css"):
            source_files.extend(root.rglob(pattern))

    assert source_files, "No UI source files found — test is looking in the wrong place"

    rel_paths = [str(p.relative_to(_REPO_ROOT)) for p in source_files]
    # --no-index evaluates the gitignore rules directly, independent of whether a
    # file happens to be staged/tracked locally — that is what CI's fresh
    # checkout sees, and what silently dropped transcript.ts.
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        input="\n".join(rel_paths),
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    ignored = [line for line in result.stdout.splitlines() if line.strip()]
    assert not ignored, (
        "These UI source files are gitignored and won't be committed, "
        f"breaking the CI build: {ignored}"
    )


def _touch(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestEnsureUiBuilt:
    """Verify launch-time UI autobuild keeps all shipped outputs aligned."""

    def test_rebuilds_portable_ui_when_web_sources_are_stale(self, tmp_path):
        """A source-triggered rebuild should refresh both normal and portable UI outputs."""
        project_root = tmp_path
        _touch(project_root / "client" / "src" / "index.ts", "export {};")
        _touch(project_root / "client" / "dist" / "index.js", "")
        _touch(project_root / "ui" / "src" / "main.tsx", "export {};")
        _touch(project_root / "ui" / "dist" / "index.html", "<html></html>")
        _touch(project_root / "src" / "chad" / "ui_dist" / "__init__.py", "")
        (project_root / "client" / "node_modules").mkdir(parents=True)
        (project_root / "ui" / "node_modules").mkdir(parents=True)

        def fake_is_stale(sources, built: Path) -> bool:
            if built == project_root / "client" / "dist" / "index.js":
                return False
            if built == project_root / "ui" / "dist" / "index.html":
                # UI bundles must consider client/src too — it is bundled in.
                assert project_root / "client" / "src" in list(sources)
                return True
            if built == project_root / "ui" / "dist-portable" / "index.html":
                assert project_root / "client" / "src" in list(sources)
                return True
            raise AssertionError(f"Unexpected stale check for {built}")

        with patch("chad.util.ui_build._find_npm", return_value="npm"), patch(
            "chad.util.ui_build._is_stale", side_effect=fake_is_stale
        ), patch("chad.util.ui_build._safe_run") as mock_run:
            ensure_ui_built(project_root=project_root, verbose=False)

        assert mock_run.call_args_list == [
            call(["npm", "run", "build"], cwd=project_root / "ui"),
            call(
                [
                    "npm",
                    "exec",
                    "--",
                    "vite",
                    "build",
                    "--config",
                    "vite.portable.config.ts",
                    "--outDir",
                    "dist-portable",
                ],
                cwd=project_root / "ui",
            ),
        ]


class TestIsStale:
    """The staleness check must consider every source tree bundled into a build."""

    def test_newer_client_src_makes_ui_dist_stale(self, tmp_path):
        """A newer file in client/src ⇒ ui/dist is stale.

        Regression: the check compared only ui/src against ui/dist, but the
        chad-client TS source is bundled into the UI, so client/src changes
        never triggered a rebuild and stale UI was served forever.
        """
        ui_src = tmp_path / "ui" / "src"
        client_src = tmp_path / "client" / "src"
        ui_dist = tmp_path / "ui" / "dist" / "index.html"
        _touch(ui_src / "main.tsx", "export {};")
        _touch(client_src / "api.ts", "export {};")
        _touch(ui_dist, "<html></html>")

        now = time.time()
        os.utime(ui_src / "main.tsx", (now - 200, now - 200))
        os.utime(ui_dist, (now - 100, now - 100))
        os.utime(client_src / "api.ts", (now, now))  # newest: only client/src changed

        assert _is_stale([ui_src, client_src], ui_dist) is True
        # Sanity: ui/src alone is older than the build.
        assert _is_stale([ui_src], ui_dist) is False

    def test_repo_ui_is_stale_considers_client_src(self, tmp_path):
        """The server-side staleness gate must also see client/src changes."""
        from chad.server.main import _repo_ui_is_stale

        ui_src = tmp_path / "ui" / "src"
        client_src = tmp_path / "client" / "src"
        ui_dist = tmp_path / "ui" / "dist" / "index.html"
        _touch(ui_src / "main.tsx", "export {};")
        _touch(client_src / "api.ts", "export {};")
        _touch(ui_dist, "<html></html>")

        now = time.time()
        os.utime(ui_src / "main.tsx", (now - 200, now - 200))
        os.utime(client_src / "api.ts", (now - 200, now - 200))
        os.utime(ui_dist, (now - 100, now - 100))

        assert _repo_ui_is_stale(tmp_path) is False

        os.utime(client_src / "api.ts", (now, now))
        assert _repo_ui_is_stale(tmp_path) is True
