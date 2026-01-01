"""Visual tests for merge conflict viewer styling.

These tests verify that the merge conflict viewer is properly styled
with side-by-side diff display, correct colors, and readable text.

Run with: pytest tests/test_merge_viewer_visual.py -v
Screenshots saved to: /tmp/chad_merge_viewer_*.png
"""

import tempfile
from pathlib import Path

import pytest


def _skip_if_no_playwright():
    """Skip test if Playwright is not available."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return False
    except ImportError:
        return True


@pytest.fixture
def chad_with_merge_viewer(tmp_path):
    """Set up Chad server and inject merge conflict content for testing."""
    if _skip_if_no_playwright():
        pytest.skip("Playwright not available")

    from chad.ui_playwright_runner import (
        create_temp_env,
        start_chad,
        stop_chad,
        open_playwright_page,
        SAMPLE_MERGE_CONFLICT_HTML,
    )

    env = create_temp_env(screenshot_mode=False)
    instance = start_chad(env)

    try:
        with open_playwright_page(
            instance.port,
            tab="run",
            headless=True,
            viewport={"width": 1400, "height": 900},
        ) as page:
            # Inject the merge conflict HTML into the page
            page.evaluate(
                """
(html) => {
    // Create a container for the merge viewer test
    const container = document.createElement('div');
    container.id = 'merge-viewer-test';
    container.style.padding = '20px';
    container.style.backgroundColor = '#2e3440';
    container.innerHTML = `
        <h2 style="color: #eceff4; margin-bottom: 16px;">Merge Conflicts Detected</h2>
        <p style="color: #d8dee9; margin-bottom: 16px;">
            2 files have conflicts that need to be resolved:
        </p>
        ${html}
        <div style="margin-top: 16px; display: flex; gap: 8px;">
            <button style="background: #5e81ac; color: white; padding: 8px 16px; border: none; border-radius: 4px;">
                Accept All Original
            </button>
            <button style="background: #a3be8c; color: #2e3440; padding: 8px 16px; border: none; border-radius: 4px;">
                Accept All Incoming
            </button>
            <button style="background: #bf616a; color: white; padding: 8px 16px; border: none; border-radius: 4px;">
                Abort Merge
            </button>
        </div>
    `;

    // Find a good place to insert - after the main content area
    const gradioApp = document.querySelector('gradio-app');
    if (gradioApp && gradioApp.shadowRoot) {
        const main = gradioApp.shadowRoot.querySelector('main') ||
                    gradioApp.shadowRoot.querySelector('.main');
        if (main) {
            main.appendChild(container);
            return true;
        }
    }

    // Fallback: append to body
    document.body.appendChild(container);
    return true;
}
""",
                SAMPLE_MERGE_CONFLICT_HTML,
            )

            # Wait for render
            page.wait_for_timeout(500)

            yield page, instance

    finally:
        stop_chad(instance)
        env.cleanup()


@pytest.mark.skipif(_skip_if_no_playwright(), reason="Playwright not available")
def test_merge_viewer_has_correct_structure(chad_with_merge_viewer):
    """Test that merge viewer renders with correct structure."""
    page, _ = chad_with_merge_viewer

    result = page.evaluate(
        """
() => {
    const viewer = document.querySelector('.conflict-viewer');
    if (!viewer) return { found: false };

    const files = viewer.querySelectorAll('.conflict-file');
    const hunks = viewer.querySelectorAll('.conflict-hunk');
    const original = viewer.querySelectorAll('.conflict-original');
    const incoming = viewer.querySelectorAll('.conflict-incoming');
    const context = viewer.querySelectorAll('.conflict-context');

    return {
        found: true,
        fileCount: files.length,
        hunkCount: hunks.length,
        originalCount: original.length,
        incomingCount: incoming.length,
        contextCount: context.length,
    };
}
"""
    )

    assert result["found"], "Conflict viewer not found in DOM"
    assert result["fileCount"] == 2, f"Expected 2 conflict files, got {result['fileCount']}"
    assert result["hunkCount"] == 2, f"Expected 2 hunks, got {result['hunkCount']}"
    assert result["originalCount"] == 2, "Expected 2 original sides"
    assert result["incomingCount"] == 2, "Expected 2 incoming sides"
    assert result["contextCount"] >= 4, "Expected context sections"


@pytest.mark.skipif(_skip_if_no_playwright(), reason="Playwright not available")
def test_merge_viewer_colors_are_distinct(chad_with_merge_viewer):
    """Test that original and incoming sides have distinct background colors."""
    page, _ = chad_with_merge_viewer

    result = page.evaluate(
        """
() => {
    const viewer = document.querySelector('.conflict-viewer');
    if (!viewer) return { found: false };

    const original = viewer.querySelector('.conflict-original');
    const incoming = viewer.querySelector('.conflict-incoming');

    if (!original || !incoming) {
        return { found: true, hasColors: false };
    }

    const origBg = window.getComputedStyle(original).backgroundColor;
    const incBg = window.getComputedStyle(incoming).backgroundColor;

    return {
        found: true,
        hasColors: true,
        originalBackground: origBg,
        incomingBackground: incBg,
        colorsAreDifferent: origBg !== incBg,
    };
}
"""
    )

    assert result["found"], "Conflict viewer not found"
    assert result["hasColors"], "Original/incoming sections not found"
    assert result["colorsAreDifferent"], (
        f"Colors should be different: original={result['originalBackground']}, "
        f"incoming={result['incomingBackground']}"
    )


@pytest.mark.skipif(_skip_if_no_playwright(), reason="Playwright not available")
def test_merge_viewer_text_is_readable(chad_with_merge_viewer):
    """Test that text in merge viewer has sufficient contrast."""
    page, _ = chad_with_merge_viewer

    result = page.evaluate(
        """
() => {
    function getBrightness(colorStr) {
        const match = colorStr.match(/rgb\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
        if (!match) return 255;
        const r = parseInt(match[1]);
        const g = parseInt(match[2]);
        const b = parseInt(match[3]);
        return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    }

    const viewer = document.querySelector('.conflict-viewer');
    if (!viewer) return { found: false };

    const codeElements = viewer.querySelectorAll('pre');
    const darkElements = [];

    for (const el of codeElements) {
        const color = window.getComputedStyle(el).color;
        const brightness = getBrightness(color);
        if (brightness < 100) {
            darkElements.push({
                text: el.textContent.substring(0, 30),
                color: color,
                brightness: brightness
            });
        }
    }

    return {
        found: true,
        totalCodeElements: codeElements.length,
        darkElementCount: darkElements.length,
        darkElements: darkElements.slice(0, 5),
        allReadable: darkElements.length === 0
    };
}
"""
    )

    assert result["found"], "Conflict viewer not found"
    assert result["allReadable"], (
        f"{result['darkElementCount']} elements have poor contrast: {result['darkElements']}"
    )


@pytest.mark.skipif(_skip_if_no_playwright(), reason="Playwright not available")
def test_merge_viewer_screenshot(chad_with_merge_viewer):
    """Take a screenshot of the merge viewer for visual verification."""
    page, _ = chad_with_merge_viewer

    # Scroll to make merge viewer visible
    page.evaluate(
        """
() => {
    const viewer = document.querySelector('#merge-viewer-test');
    if (viewer) {
        viewer.scrollIntoView({ behavior: 'instant', block: 'start' });
    }
}
"""
    )
    page.wait_for_timeout(300)

    # Take screenshot
    screenshot_dir = Path(tempfile.gettempdir())
    screenshot_path = screenshot_dir / "chad_merge_viewer_test.png"

    # Try to screenshot just the merge viewer container
    element = page.query_selector("#merge-viewer-test")
    if element:
        element.screenshot(path=str(screenshot_path))
    else:
        # Fallback to full page
        page.screenshot(path=str(screenshot_path))

    assert screenshot_path.exists(), f"Screenshot not created at {screenshot_path}"
    assert screenshot_path.stat().st_size > 1000, "Screenshot file too small"

    print(f"\nScreenshot saved to: {screenshot_path}")


@pytest.mark.skipif(_skip_if_no_playwright(), reason="Playwright not available")
def test_merge_viewer_file_headers_visible(chad_with_merge_viewer):
    """Test that file headers are properly displayed."""
    page, _ = chad_with_merge_viewer

    result = page.evaluate(
        """
() => {
    const viewer = document.querySelector('.conflict-viewer');
    if (!viewer) return { found: false };

    const headers = viewer.querySelectorAll('.conflict-file-header');
    const headerTexts = [];

    for (const header of headers) {
        const text = header.textContent.trim();
        const computed = window.getComputedStyle(header);
        headerTexts.push({
            text: text,
            color: computed.color,
            fontSize: computed.fontSize
        });
    }

    return {
        found: true,
        headerCount: headers.length,
        headers: headerTexts
    };
}
"""
    )

    assert result["found"], "Conflict viewer not found"
    assert result["headerCount"] == 2, f"Expected 2 file headers, got {result['headerCount']}"

    header_texts = [h["text"] for h in result["headers"]]
    assert "src/auth/login.py" in header_texts, "login.py header not found"
    assert "tests/test_auth.py" in header_texts, "test_auth.py header not found"


@pytest.fixture
def chad_with_diff_viewer(tmp_path):
    """Set up Chad server and inject side-by-side diff content for testing."""
    if _skip_if_no_playwright():
        pytest.skip("Playwright not available")

    from chad.ui_playwright_runner import (
        create_temp_env,
        start_chad,
        stop_chad,
        open_playwright_page,
        SAMPLE_DIFF_HTML,
    )

    env = create_temp_env(screenshot_mode=False)
    instance = start_chad(env)

    try:
        with open_playwright_page(
            instance.port,
            tab="run",
            headless=True,
            viewport={"width": 1400, "height": 900},
        ) as page:
            # Inject the diff HTML into the page
            page.evaluate(
                """
(html) => {
    const container = document.createElement('div');
    container.id = 'diff-viewer-test';
    container.style.padding = '20px';
    container.style.backgroundColor = '#2e3440';
    container.innerHTML = `
        <h2 style="color: #eceff4; margin-bottom: 16px;">Changes to Merge</h2>
        <p style="color: #d8dee9; margin-bottom: 16px;">
            2 files changed, 15 insertions(+), 4 deletions(-)
        </p>
        ${html}
    `;
    document.body.appendChild(container);
    return true;
}
""",
                SAMPLE_DIFF_HTML,
            )

            page.wait_for_timeout(500)
            yield page, instance

    finally:
        stop_chad(instance)
        env.cleanup()


@pytest.mark.skipif(_skip_if_no_playwright(), reason="Playwright not available")
def test_diff_viewer_has_side_by_side_structure(chad_with_diff_viewer):
    """Test that diff viewer renders with proper side-by-side structure."""
    page, _ = chad_with_diff_viewer

    result = page.evaluate(
        """
() => {
    const viewer = document.querySelector('.diff-viewer');
    if (!viewer) return { found: false };

    const files = viewer.querySelectorAll('.diff-file');
    const comparisons = viewer.querySelectorAll('.diff-comparison');
    const leftSides = viewer.querySelectorAll('.diff-side-left');
    const rightSides = viewer.querySelectorAll('.diff-side-right');

    // Check for side-by-side layout
    let isSideBySide = false;
    for (const comparison of comparisons) {
        const style = window.getComputedStyle(comparison);
        if (style.display === 'flex') {
            isSideBySide = true;
            break;
        }
    }

    return {
        found: true,
        fileCount: files.length,
        comparisonCount: comparisons.length,
        leftSideCount: leftSides.length,
        rightSideCount: rightSides.length,
        isSideBySide: isSideBySide,
    };
}
"""
    )

    assert result["found"], "Diff viewer not found in DOM"
    assert result["fileCount"] == 2, f"Expected 2 files, got {result['fileCount']}"
    assert result["leftSideCount"] >= 1, "No left sides found"
    assert result["rightSideCount"] >= 1, "No right sides found"
    assert result["isSideBySide"], "Diff comparison not using flex layout (side-by-side)"


@pytest.mark.skipif(_skip_if_no_playwright(), reason="Playwright not available")
def test_diff_viewer_has_correct_line_colors(chad_with_diff_viewer):
    """Test that added/removed/context lines have correct styling."""
    page, _ = chad_with_diff_viewer

    result = page.evaluate(
        """
() => {
    const viewer = document.querySelector('.diff-viewer');
    if (!viewer) return { found: false };

    const addedLines = viewer.querySelectorAll('.diff-line.added');
    const removedLines = viewer.querySelectorAll('.diff-line.removed');
    const contextLines = viewer.querySelectorAll('.diff-line.context');

    let addedBg = '';
    let removedBg = '';

    if (addedLines.length > 0) {
        addedBg = window.getComputedStyle(addedLines[0]).backgroundColor;
    }
    if (removedLines.length > 0) {
        removedBg = window.getComputedStyle(removedLines[0]).backgroundColor;
    }

    return {
        found: true,
        addedCount: addedLines.length,
        removedCount: removedLines.length,
        contextCount: contextLines.length,
        addedBackground: addedBg,
        removedBackground: removedBg,
        colorsAreDifferent: addedBg !== removedBg,
    };
}
"""
    )

    assert result["found"], "Diff viewer not found"
    assert result["addedCount"] > 0, "No added lines found"
    assert result["removedCount"] > 0, "No removed lines found"
    assert result["colorsAreDifferent"], (
        f"Added and removed should have different backgrounds: "
        f"added={result['addedBackground']}, removed={result['removedBackground']}"
    )


@pytest.mark.skipif(_skip_if_no_playwright(), reason="Playwright not available")
def test_diff_viewer_screenshot(chad_with_diff_viewer):
    """Take a screenshot of the diff viewer for visual verification."""
    page, _ = chad_with_diff_viewer

    # Scroll to make diff viewer visible
    page.evaluate(
        """
() => {
    const viewer = document.querySelector('#diff-viewer-test');
    if (viewer) {
        viewer.scrollIntoView({ behavior: 'instant', block: 'start' });
    }
}
"""
    )
    page.wait_for_timeout(300)

    screenshot_dir = Path(tempfile.gettempdir())
    screenshot_path = screenshot_dir / "chad_diff_viewer_test.png"

    element = page.query_selector("#diff-viewer-test")
    if element:
        element.screenshot(path=str(screenshot_path))
    else:
        page.screenshot(path=str(screenshot_path))

    assert screenshot_path.exists(), f"Screenshot not created at {screenshot_path}"
    assert screenshot_path.stat().st_size > 1000, "Screenshot file too small"

    print(f"\nScreenshot saved to: {screenshot_path}")
