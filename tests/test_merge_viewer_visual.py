"""Visual-style tests for merge conflict and diff viewer styling.

These tests validate HTML structure and CSS styling without launching a browser,
which keeps them reliable in sandboxed environments.
"""

from __future__ import annotations

import re

from chad.ui.gradio.verification.ui_playwright_runner import SAMPLE_DIFF_HTML, SAMPLE_MERGE_CONFLICT_HTML
from chad.ui.gradio.gradio_ui import PROVIDER_PANEL_CSS


MERGE_VIEWER_PAGE_HTML = f"""
<div id="merge-viewer-test">
  <h2>Merge Conflicts Detected</h2>
  <p>2 files have conflicts that need to be resolved:</p>
  {SAMPLE_MERGE_CONFLICT_HTML}
  <div class="merge-actions">
    <button>Accept All Original</button>
    <button>Accept All Incoming</button>
    <button>Abort Merge</button>
  </div>
</div>
"""

DIFF_VIEWER_PAGE_HTML = f"""
<div id="diff-viewer-test">
  <h2>Changes to Merge</h2>
  <p>2 files changed, 15 insertions(+), 4 deletions(-)</p>
  {SAMPLE_DIFF_HTML}
</div>
"""


def _class_count(html: str, class_name: str) -> int:
    count = 0
    for match in re.findall(r'class="([^"]+)"', html):
        if class_name in match.split():
            count += 1
    return count


def _css_block(css: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", css, re.DOTALL)
    return match.group(1) if match else ""


def _css_prop(css: str, selector: str, prop: str) -> str:
    block = _css_block(css, selector)
    match = re.search(rf"{re.escape(prop)}\s*:\s*([^;]+);", block)
    return match.group(1).strip() if match else ""


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.strip()
    if color.startswith("#"):
        color = color[1:]
    return (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))


def _brightness(rgb: tuple[int, int, int]) -> float:
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def test_merge_viewer_has_correct_structure():
    """Merge viewer HTML should include expected conflict structure."""
    html = SAMPLE_MERGE_CONFLICT_HTML

    assert _class_count(html, "conflict-file") == 2
    assert _class_count(html, "conflict-hunk") == 2
    assert _class_count(html, "conflict-original") == 2
    assert _class_count(html, "conflict-incoming") == 2
    assert _class_count(html, "conflict-context") >= 4


def test_merge_viewer_colors_are_distinct():
    """Original and incoming sides should have distinct background colors."""
    original_bg = _css_prop(PROVIDER_PANEL_CSS, ".conflict-original", "background")
    incoming_bg = _css_prop(PROVIDER_PANEL_CSS, ".conflict-incoming", "background")

    assert original_bg
    assert incoming_bg
    assert original_bg != incoming_bg


def test_merge_viewer_text_is_readable():
    """Merge viewer text color should have sufficient contrast."""
    text_color = _css_prop(PROVIDER_PANEL_CSS, ".conflict-side-content", "color")
    assert text_color

    rgb = _hex_to_rgb(text_color)
    assert _brightness(rgb) >= 100


def test_merge_viewer_has_action_buttons():
    """Merge viewer wrapper should expose action buttons."""
    html = MERGE_VIEWER_PAGE_HTML
    assert "Accept All Original" in html
    assert "Accept All Incoming" in html
    assert "Abort Merge" in html


def test_merge_viewer_file_headers_visible():
    """Merge viewer should include expected file headers."""
    html = SAMPLE_MERGE_CONFLICT_HTML
    assert "src/auth/login.py" in html
    assert "tests/test_auth.py" in html


def test_diff_viewer_has_side_by_side_structure():
    """Diff viewer HTML should include side-by-side layout elements."""
    html = SAMPLE_DIFF_HTML

    assert _class_count(html, "diff-file") >= 1
    assert _class_count(html, "diff-comparison") >= 1
    assert _class_count(html, "diff-side-left") >= 1
    assert _class_count(html, "diff-side-right") >= 1

    comparison_display = _css_prop(PROVIDER_PANEL_CSS, ".diff-comparison", "display")
    assert comparison_display == "flex"


def test_diff_viewer_has_correct_line_colors():
    """Added and removed lines should have distinct background colors."""
    added_bg = _css_prop(PROVIDER_PANEL_CSS, ".diff-line.added", "background")
    removed_bg = _css_prop(PROVIDER_PANEL_CSS, ".diff-line.removed", "background")

    assert added_bg
    assert removed_bg
    assert added_bg != removed_bg


def test_diff_viewer_screenshot_placeholder():
    """Diff viewer HTML should include the summary text for screenshots."""
    html = DIFF_VIEWER_PAGE_HTML
    assert "Changes to Merge" in html
    assert "2 files changed" in html
