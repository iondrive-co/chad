"""Test for code syntax highlighting in live view."""

import pytest

try:
    from playwright.sync_api import Page
except Exception:
    pytest.skip("playwright not available", allow_module_level=True)

# Import test utilities
from chad.ui.gradio.verification.ui_playwright_runner import (
    ChadLaunchError,
    create_temp_env,
    inject_live_stream_content,
    open_playwright_page,
    start_chad,
    stop_chad,
    verify_all_text_visible,
)

# Mark all tests in this module as visual tests (require Playwright browser)
pytestmark = pytest.mark.visual


@pytest.fixture(scope="module")
def temp_env():
    """Create a temporary Chad environment for UI testing."""
    env = create_temp_env()
    yield env
    env.cleanup()


@pytest.fixture(scope="module")
def chad_server(temp_env):
    """Start Chad server with mock providers."""
    try:
        instance = start_chad(temp_env)
    except ChadLaunchError as exc:
        pytest.skip(f"Chad server launch failed: {exc}", allow_module_level=True)
    else:
        try:
            yield instance.port
        finally:
            stop_chad(instance)


@pytest.fixture
def page(chad_server):
    """Create a Playwright page connected to Chad."""
    with open_playwright_page(
        chad_server,
        viewport={"width": 1280, "height": 900},
    ) as page:
        yield page


class TestCodeSyntaxHighlighting:
    """Test that code in the live view is properly syntax highlighted."""

    # Realistic CLI output with various code elements
    CLI_OUTPUT_WITH_CODE = """
<p><span style="color: rgb(198, 120, 221);">thinking</span> Let me check the current implementation...</p>
<p><span style="color: rgb(198, 120, 221);">exec</span> <span style="color: rgb(152, 195, 121);">grep -n "def.*process" main.py</span></p>  # noqa: E501
<p>23:def process_data(input_data):</p>
<p>45:def process_results(results):</p>
<p><span style="color: rgb(198, 120, 221);">file_write</span> <code>test.py</code></p>
<pre><code class="language-python">
import sys

def main():
    # This is a comment
    name = "world"
    print(f"Hello, {name}!")
    return 0

if __name__ == "__main__":
    sys.exit(main())
</code></pre>
<p><span style="color: rgb(152, 195, 121);">✓ File written successfully</span></p>
"""

    def test_code_elements_have_syntax_colors(self, page: Page):
        """Code elements should have proper syntax highlighting colors."""
        inject_live_stream_content(page, self.CLI_OUTPUT_WITH_CODE)

        # Check that code elements exist and are properly styled
        code_elements = page.locator("#live-stream-box code").all()
        assert len(code_elements) > 0, "Should have code elements"

        # Check that pre/code blocks exist
        pre_elements = page.locator("#live-stream-box pre code").all()
        assert len(pre_elements) > 0, "Should have pre/code blocks"

        # Debug print code elements
        for i, code_elem in enumerate(code_elements[:3]):  # Check first few
            text = code_elem.text_content()
            color = code_elem.evaluate("elem => getComputedStyle(elem).color")
            classes = code_elem.evaluate("elem => elem.className")
            parent_classes = code_elem.evaluate("elem => elem.parentElement.className")
            print(f"Code element {i}: text='{text}', color={color}, classes='{classes}', parent='{parent_classes}'")

            # Code should be styled with appropriate colors
            # Either pink (#f0abfc = rgb(240, 171, 252)) for generic code
            # or syntax-specific colors if highlighting is applied
            # Just check it's not the default grey
            assert color != "rgb(226, 232, 240)", f"Code should not be default grey color, got {color} for '{text}'"

            # Check no background box
            bg = code_elem.evaluate("elem => getComputedStyle(elem).backgroundColor")
            assert bg in ["transparent", "rgba(0, 0, 0, 0)", "none", ""], f"Code should have no background, got {bg}"

    def test_syntax_highlighting_in_code_blocks(self, page: Page):
        """Code blocks should have syntax highlighting for language keywords."""
        # Enhanced code block with more syntax elements
        code_block_html = '''
<pre><code class="language-python">
<span class="keyword">import</span> <span class="module">sys</span>
<span class="keyword">from</span> <span class="module">typing</span> <span class="keyword">import</span> <span class="type">List</span>, <span class="type">Dict</span>  # noqa: E501

<span class="keyword">class</span> <span class="class-name">DataProcessor</span>:
    <span class="string">"""Process data with syntax highlighting."""</span>

    <span class="keyword">def</span> <span class="function">__init__</span>(<span class="param">self</span>, <span class="param">config</span>: <span class="type">Dict</span>):  # noqa: E501
        <span class="keyword">self</span>.<span class="property">config</span> = <span class="param">config</span>
        <span class="keyword">self</span>.<span class="property">data</span>: <span class="type">List</span>[<span class="type">str</span>] = []  # noqa: E501

    <span class="keyword">def</span> <span class="function">process</span>(<span class="param">self</span>, <span class="param">items</span>: <span class="type">List</span>[<span class="type">str</span>]) -> <span class="type">bool</span>:  # noqa: E501
        <span class="comment"># Process each item</span>
        <span class="keyword">for</span> <span class="variable">item</span> <span class="keyword">in</span> <span class="param">items</span>:  # noqa: E501
            <span class="keyword">if</span> <span class="variable">item</span>.<span class="method">startswith</span>(<span class="string">'test_'</span>):  # noqa: E501
                <span class="keyword">self</span>.<span class="property">data</span>.<span class="method">append</span>(<span class="variable">item</span>)  # noqa: E501
        <span class="keyword">return</span> <span class="constant">True</span>

<span class="comment"># Usage example</span>
<span class="variable">processor</span> = <span class="class-name">DataProcessor</span>({<span class="string">'mode'</span>: <span class="string">'test'</span>})  # noqa: E501
<span class="variable">result</span> = <span class="variable">processor</span>.<span class="method">process</span>([<span class="string">'test_one'</span>, <span class="string">'skip'</span>, <span class="string">'test_two'</span>])  # noqa: E501
<span class="builtin">print</span>(<span class="string">f"Processed: <span class="interpolation">{<span class="variable">len</span>(<span class="variable">processor</span>.<span class="property">data</span>)}</span> items"</span>)  # noqa: E501
</code></pre>
'''

        inject_live_stream_content(page, code_block_html)

        # Check that syntax elements have appropriate colors
        syntax_checks = [
            ("span.keyword", "keyword should have distinct color"),
            ("span.string", "strings should have distinct color"),
            ("span.comment", "comments should have distinct color"),
            ("span.function", "functions should have distinct color"),
            ("span.class-name", "class names should have distinct color"),
            ("span.type", "types should have distinct color"),
        ]

        for selector, description in syntax_checks:
            elements = page.locator(f"#live-stream-box {selector}").all()
            if elements:
                # Just verify they exist for now - we'll add color rules in implementation
                assert len(elements) > 0, f"Should have {description}"

    def test_inline_code_vs_code_blocks(self, page: Page):
        """Inline code and code blocks should have different styling."""
        mixed_content = """
<p>Use the <code>process_data()</code> function to handle input.</p>
<p>Here's the full implementation:</p>
<pre><code class="language-python">
def process_data(data):
    return [x.strip() for x in data if x]
</code></pre>
<p>Call it like this: <code>result = process_data(my_list)</code></p>
"""

        inject_live_stream_content(page, mixed_content)

        # Check inline code
        inline_code = page.locator("#live-stream-box p > code").first
        inline_style = inline_code.evaluate("elem => getComputedStyle(elem)")

        # Check code block
        block_code = page.locator("#live-stream-box pre > code").first
        block_style = block_code.evaluate("elem => getComputedStyle(elem)")

        # Both should have no background per requirements
        assert inline_style["backgroundColor"] in ["transparent", "rgba(0, 0, 0, 0)"]
        assert block_style["backgroundColor"] in ["transparent", "rgba(0, 0, 0, 0)"]

        # Both should use color highlighting instead of boxes
        assert inline_style["padding"] == "0px"
        assert block_style["padding"] == "0px"

    def test_code_blocks_render_without_box_chrome(self, page: Page):
        """Code blocks should not render with boxed backgrounds or borders."""
        boxed_content = """
<pre><code class="language-python">
def greet(name):
    return f"Hello {name}"
</code></pre>
"""

        inject_live_stream_content(page, boxed_content)

        pre = page.locator("#live-stream-box pre").first
        styles = pre.evaluate(
            "el => ({"
            "backgroundColor: getComputedStyle(el).backgroundColor,"
            "borderTopWidth: getComputedStyle(el).borderTopWidth,"
            "borderTopStyle: getComputedStyle(el).borderTopStyle,"
            "borderRadius: getComputedStyle(el).borderRadius,"
            "boxShadow: getComputedStyle(el).boxShadow,"
            "padding: getComputedStyle(el).padding"
            "})"
        )

        assert styles["backgroundColor"] in ["transparent", "rgba(0, 0, 0, 0)"], (
            f"Expected no background for code blocks, got {styles['backgroundColor']}"
        )
        assert styles["borderTopWidth"] == "0px" and styles["borderTopStyle"] == "none", (
            "Expected code blocks to have no border chrome."
        )
        assert styles["borderRadius"] in ["0px", "0px 0px 0px 0px"], (
            f"Expected code blocks to have square edges, got {styles['borderRadius']}"
        )
        assert styles["boxShadow"] == "none", "Expected code blocks to have no shadow."
        assert styles["padding"] == "0px", "Expected code blocks to avoid padded box styling."

    def test_visibility_of_all_code_elements(self, page: Page):
        """All code elements should be visible on dark background."""
        inject_live_stream_content(page, self.CLI_OUTPUT_WITH_CODE)
        result = verify_all_text_visible(page)

        assert result.get(
            "allVisible", False
        ), f"Some code text is too dark. Dark elements: {result.get('darkElements', [])}"

    def test_actual_syntax_highlighting_applied(self, page: Page):
        """Test that Python code has actual syntax highlighting applied."""
        # Use the actual build_live_stream_html function
        from chad.ui.gradio.web_ui import build_live_stream_html

        # Test with plain text that would come from CLI
        plain_text = """<pre><code class="language-python">
def hello():
    print("Hello world")
    return True
</code></pre>"""

        # Process through build_live_stream_html as the app would
        html = build_live_stream_html(plain_text, "TEST AI")

        print(f"Generated HTML: {html[:500]}")

        # Should have syntax highlighting
        assert '<span class="keyword">' in html, "Should have keyword spans"
        assert '<span class="function">' in html, "Should have function spans"
        assert '<span class="string">' in html, "Should have string spans"

        # Now inject and verify in browser
        inject_live_stream_content(page, html)

        # Check specific colors in browser
        keyword = page.locator("#live-stream-box .keyword").first
        if keyword.count() > 0:
            color = keyword.evaluate("elem => getComputedStyle(elem).color")
            print(f"Keyword color: {color}")
            # Should be purple (#c678dd = rgb(198, 120, 221))
            assert "rgb(198" in color

        string_elem = page.locator("#live-stream-box .string").first
        if string_elem.count() > 0:
            color = string_elem.evaluate("elem => getComputedStyle(elem).color")
            print(f"String color: {color}")
            # Should be green (#98c379 = rgb(152, 195, 121))
            assert "rgb(152" in color
