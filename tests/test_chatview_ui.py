"""Tests for ChatView UI behavior and CSS styling.

These tests verify that:
1. Follow-up tasks preserve conversation milestones
2. Screenshots are displayed in milestones
3. Milestone text is expandable when truncated
4. Verification agent picker is editable when None
"""

import re
from pathlib import Path


# File paths
UI_DIR = Path(__file__).parent.parent / "ui" / "src"
CHATVIEW_FILE = UI_DIR / "components" / "ChatView.tsx"
CSS_FILE = UI_DIR / "styles" / "main.css"
TYPES_FILE = Path(__file__).parent.parent / "client" / "src" / "types.ts"


class TestFollowUpPreservesConversation:
    """Verify that follow-up tasks don't clear conversation history."""

    def test_handle_task_start_does_not_clear_conversation_on_followup(self):
        """handleTaskStart should not clear conversation when it's a follow-up."""
        content = CHATVIEW_FILE.read_text()

        # Find the handleTaskStart function - it should have isFollowup parameter
        # and conditionally clear conversation
        handle_task_start_match = re.search(
            r"const handleTaskStart = useCallback\(async \([^)]+\) => \{",
            content,
        )
        assert handle_task_start_match, "Should have handleTaskStart function"

        # Get the full function body
        start = handle_task_start_match.end()
        brace_count = 1
        end = start
        for i, char in enumerate(content[start:], start):
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    end = i
                    break

        func_body = content[handle_task_start_match.start():end]

        # The function should have isFollowup parameter and check it
        assert "isFollowup" in func_body, (
            "handleTaskStart should have isFollowup parameter"
        )

        # setConversation([]) should be conditional based on isFollowup
        assert "!isFollowup" in func_body or "if (!isFollowup)" in func_body, (
            "handleTaskStart should check isFollowup before clearing conversation"
        )

    def test_streaming_session_started_preserves_milestones_on_followup(self):
        """session_started event handler should preserve milestones when hasRunTask is true."""
        content = CHATVIEW_FILE.read_text()

        # Find the streaming event handler that processes session_started
        # It should not unconditionally set updated = []
        # Instead, it should preserve existing items when hasRunTask is true

        # Look for the session_started handling code
        session_started_pattern = r'if \(evtType === "session_started"\) \{'
        match = re.search(session_started_pattern, content)
        assert match, "Should have session_started event handler"

        # Get context around the match
        start = match.start()
        # Find the closing brace of this if block
        brace_count = 0
        end = start
        for i, char in enumerate(content[start:], start):
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    end = i
                    break

        handler_block = content[start:end]

        # The handler should NOT have unconditional "updated = []"
        # It should either not clear, or check a condition
        if "updated = []" in handler_block:
            # If clearing, should have a condition
            # This is acceptable if it's resetting on first task only
            # Look for something like checking hasRunTask or conversation length
            assert (
                "hasRunTask" in handler_block or
                "prev.length" in handler_block or
                "conversation.length" in handler_block or
                "// Clear" in handler_block  # Explicit comment explaining why
            ), (
                "session_started handler should preserve conversation on follow-up, "
                "not unconditionally clear with 'updated = []'"
            )


class TestMilestoneScreenshots:
    """Verify that screenshots can be displayed in milestone bubbles."""

    def test_milestone_item_has_screenshots_field(self):
        """ConversationItem type should support screenshots in milestones."""
        content = TYPES_FILE.read_text()

        # Find ConversationItem interface
        conv_item_match = re.search(
            r"export interface ConversationItem \{([^}]+)\}",
            content,
            re.DOTALL,
        )
        assert conv_item_match, "Should have ConversationItem interface"

        interface_body = conv_item_match.group(1)

        # Should have a screenshots field for milestones
        assert "screenshots" in interface_body, (
            "ConversationItem should have screenshots field for milestone images"
        )

    def test_chatview_renders_milestone_screenshots(self):
        """ChatView should render screenshots in milestone bubbles."""
        content = CHATVIEW_FILE.read_text()

        # Look for screenshot rendering in milestone bubbles
        # The chat-bubble for milestones should show images if screenshots exist
        assert "screenshots" in content, (
            "ChatView should handle screenshots in milestone display"
        )


class TestExpandableMilestones:
    """Verify that milestone messages are expandable when truncated."""

    def test_clamped_class_exists_in_css(self):
        """CSS should have .clamped class for truncated text."""
        content = CSS_FILE.read_text()

        # Find .chat-bubble-text.clamped rule
        match = re.search(
            r"\.chat-bubble-text\.clamped\s*\{([^}]+)\}",
            content,
        )
        assert match, "Should have .chat-bubble-text.clamped CSS rule"

        rule_content = match.group(1)

        # Should have line-clamp for truncation
        assert "-webkit-line-clamp" in rule_content or "line-clamp" in rule_content, (
            "Clamped class should use line-clamp for text truncation"
        )

    def test_milestone_bubble_is_clickable(self):
        """Milestone bubbles should have clickable class for expand/collapse."""
        content = CSS_FILE.read_text()

        # Check for .chat-bubble.clickable rule
        match = re.search(
            r"\.chat-bubble\.clickable\s*\{([^}]+)\}",
            content,
        )
        assert match, "Should have .chat-bubble.clickable CSS rule"

        rule_content = match.group(1)
        assert "cursor: pointer" in rule_content, (
            "Clickable bubbles should have cursor: pointer"
        )

    def test_chatview_has_expand_state_for_milestones(self):
        """ChatView should track expanded state for milestones."""
        content = CHATVIEW_FILE.read_text()

        # Should have expandedMilestones state
        assert "expandedMilestones" in content, (
            "ChatView should track which milestones are expanded"
        )

        # Should have toggle logic
        assert "setExpandedMilestones" in content, (
            "ChatView should have setter for expanded milestones"
        )

    def test_milestone_bubble_applies_clamped_conditionally(self):
        """Milestone text should be clamped only when not expanded."""
        content = CHATVIEW_FILE.read_text()

        # Look for conditional clamped class application
        # Should see pattern like: isMilestone && !isExpanded ? " clamped" : ""
        assert (
            'isMilestone && !isExpanded ? " clamped"' in content or
            '!isExpanded ? "clamped"' in content or
            "isExpanded" in content
        ), (
            "Milestone text should conditionally apply clamped class based on expansion state"
        )

    def test_milestone_has_expand_hint(self):
        """Milestones should have a visible expand/collapse hint."""
        content = CHATVIEW_FILE.read_text()

        # Should have expand hint element
        assert "expand-hint" in content, (
            "Milestones should have expand/collapse hint indicator"
        )

    def test_expand_hint_css_exists(self):
        """CSS should have styling for expand hint."""
        content = CSS_FILE.read_text()

        assert ".chat-bubble-expand-hint" in content, (
            "Should have CSS for chat-bubble-expand-hint"
        )


class TestVerificationAgentPicker:
    """Verify that verification agent picker is editable when None."""

    def test_verification_picker_not_disabled_when_enabled(self):
        """Verification picker should be editable when verification is enabled."""
        content = CHATVIEW_FILE.read_text()

        # Find the AccountPicker for verification
        # It should only be disabled when verificationSettings?.enabled === false
        # NOT when verificationAccount is null

        # Look for the AccountPicker with disabled prop
        picker_pattern = r'<AccountPicker[^>]*selected=\{[^}]*verificationAccount[^}]*\}[^>]*disabled='
        match = re.search(picker_pattern, content, re.DOTALL)
        assert match, "Should have AccountPicker for verification with disabled prop"

        # Get the full AccountPicker element
        start = match.start()
        # Find closing />
        tag_end = content.find("/>", start)
        picker_element = content[start:tag_end + 2]

        # The disabled prop should NOT check verificationAccount === null
        # It should only check verificationSettings?.enabled === false
        assert "verificationSettings?.enabled === false" in picker_element, (
            "Verification picker should only be disabled when verification is disabled globally"
        )

        # Should NOT have verificationAccount in disabled condition
        # Split out just the disabled prop value
        disabled_match = re.search(r'disabled=\{([^}]+)\}', picker_element)
        if disabled_match:
            disabled_value = disabled_match.group(1)
            assert "verificationAccount" not in disabled_value, (
                "Verification picker disabled state should not depend on verificationAccount being null"
            )

    def test_verification_picker_allows_none_option(self):
        """Verification picker should allow selecting None option."""
        content = CHATVIEW_FILE.read_text()

        # The AccountPicker should have allowNone prop
        assert "allowNone" in content, (
            "Verification picker should have allowNone prop for optional selection"
        )
