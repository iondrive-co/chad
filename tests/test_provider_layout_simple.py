"""Simple test for provider layout improvements."""

import pytest
from pathlib import Path


def test_provider_cards_wrapped_in_row():
    """Test that provider cards are wrapped in a Row container."""
    web_ui_path = Path(__file__).parent.parent / "src/chad/web_ui.py"

    # Read the web_ui.py file
    content = web_ui_path.read_text()

    # Check that provider cards are created within a Row
    # Look for the pattern where provider cards are created
    assert "with gr.Row(" in content, "Provider cards should be wrapped in gr.Row"

    # Check that Row has proper attributes for two-column layout
    # Look for equal_height=True which ensures cards have same height
    if "for idx in range(self.provider_card_count):" in content:
        # Find the section where provider cards are created
        start_idx = content.find("for idx in range(self.provider_card_count):")

        # Look backwards from the loop to find the Row
        row_section = content[:start_idx]
        last_row_idx = row_section.rfind("with gr.Row(")

        if last_row_idx != -1:
            # Extract the Row parameters
            row_end = content.find(":", last_row_idx)
            row_line = content[last_row_idx:row_end]

            # Check for equal_height=True
            assert "equal_height=True" in row_line, "Row should have equal_height=True for consistent card heights"
        else:
            pytest.fail("Provider cards should be created within a gr.Row context")


def test_css_has_reduced_padding():
    """Test that CSS has reduced padding values."""
    web_ui_path = Path(__file__).parent.parent / "src/chad/web_ui.py"
    content = web_ui_path.read_text()

    # Check provider-usage padding
    usage_idx = content.find(".provider-usage {")
    if usage_idx != -1:
        # Find the closing brace
        close_idx = content.find("}", usage_idx)
        usage_css = content[usage_idx:close_idx]

        # Original padding was "10px 12px"
        assert "padding: 10px 12px" not in usage_css, "Provider usage padding should be reduced from 10px 12px"

        # Should have some padding defined
        assert "padding:" in usage_css, "Provider usage should still have padding defined"

    # Check provider-card padding
    card_idx = content.find(".provider-card {")
    if card_idx != -1:
        close_idx = content.find("}", card_idx)
        card_css = content[card_idx:close_idx]

        # Original padding was "14px 16px"
        assert "padding: 14px 16px" not in card_css, "Provider card padding should be reduced from 14px 16px"

        # Should have some padding defined
        assert "padding:" in card_css, "Provider card should still have padding defined"

    # Check provider-usage-title margin
    title_idx = content.find(".provider-usage-title {")
    if title_idx != -1:
        close_idx = content.find("}", title_idx)
        title_css = content[title_idx:close_idx]

        # Original margin-top was "10px"
        assert "margin-top: 10px" not in title_css, "Usage title margin should be reduced from 10px"


def test_css_grid_or_flexbox_layout():
    """Test that CSS includes grid or flexbox properties for two-column layout."""
    web_ui_path = Path(__file__).parent.parent / "src/chad/web_ui.py"
    content = web_ui_path.read_text()

    # Check for CSS that enables multi-column layout
    # This could be grid-template-columns, flex properties, or column-count
    css_section = content[content.find("custom_css"):content.find("</style>")] if "</style>" in content else content

    # Look for indicators of multi-column layout
    multi_col_indicators = [
        "grid-template-columns",
        "column-count: 2",
        "flex-basis: 50%",
        "width: 50%",
        "calc(50%",
    ]

    found_indicator = any(indicator in css_section for indicator in multi_col_indicators)

    # Also check if Gradio Row is used with proper settings
    has_row_with_cols = "with gr.Row(" in content and ("elem_classes" in content or "equal_height=True" in content)

    assert found_indicator or has_row_with_cols, "Should have CSS or layout configuration for two-column display"
