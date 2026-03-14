"""Tests for the UI autobuild helper."""

from pathlib import Path
from unittest.mock import call, patch

from chad.util.ui_build import ensure_ui_built


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

        def fake_is_stale(src: Path, built: Path) -> bool:
            if built == project_root / "client" / "dist" / "index.js":
                return False
            if built == project_root / "ui" / "dist" / "index.html":
                return True
            if built == project_root / "ui" / "dist-portable" / "index.html":
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
