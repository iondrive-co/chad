"""Type-safe UI state management for merge/changes panel.

This module provides a MergeUIState dataclass that replaces the fragile
15-element tuple pattern used for Gradio UI updates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List

import gradio as gr


@dataclass
class MergeUIState:
    """Complete UI state for merge/changes panel - single source of truth.

    This dataclass encapsulates all 15 UI elements that need to be updated
    together during merge operations. The to_gradio_updates() method converts
    to the tuple format expected by Gradio event handlers.

    Index mapping:
        0: merge_section - Column visibility
        1: changes_summary - Markdown content
        2: conflict_section - Column visibility
        3: conflict_info - Markdown content
        4: conflicts_html - HTML content
        5: task_status - Markdown content
        6: chatbot - Chat history list
        7: start_btn - Button interactivity
        8: cancel_btn - Button interactivity
        9: live_stream - HTML content
        10: followup_row - Row visibility
        11: task_description - Textbox value
        12: merge_visibility_state - JS visibility control ("visible"/"hidden")
        13: merge_section_header - Markdown content
        14: diff_content - HTML content
    """

    merge_visible: bool = False
    changes_summary: str = ""
    conflict_visible: bool = False
    conflict_info: str = ""
    conflicts_html: str = ""
    status_message: str = ""
    chatbot_history: List[Any] = field(default_factory=list)
    start_enabled: bool = True
    cancel_enabled: bool = False
    live_stream: str = ""
    followup_visible: bool = False
    task_description: str = ""
    merge_header: str = ""
    diff_content: str = ""

    def to_gradio_updates(self) -> tuple:
        """Convert to Gradio update tuple - single place that defines ordering.

        This is the ONLY place where the 15-element tuple structure is defined,
        ensuring consistency across all merge operations.
        """
        visibility_state = "visible" if self.merge_visible else "hidden"
        return (
            gr.update(visible=self.merge_visible),  # 0: merge_section
            self.changes_summary,                    # 1: changes_summary
            gr.update(visible=self.conflict_visible),  # 2: conflict_section
            self.conflict_info,                      # 3: conflict_info
            self.conflicts_html,                     # 4: conflicts_html
            gr.update(value=self.status_message, visible=bool(self.status_message)),  # 5: task_status
            self.chatbot_history,                    # 6: chatbot
            gr.update(interactive=self.start_enabled),  # 7: start_btn
            gr.update(interactive=self.cancel_enabled),  # 8: cancel_btn
            self.live_stream,                        # 9: live_stream
            gr.update(visible=self.followup_visible),  # 10: followup_row
            self.task_description,                   # 11: task_description
            visibility_state,                        # 12: merge_visibility_state
            self.merge_header,                       # 13: merge_section_header
            self.diff_content,                       # 14: diff_content
        )

    @classmethod
    def reset_after_merge(cls, status: str) -> "MergeUIState":
        """Factory for post-merge reset state."""
        return cls(
            merge_visible=False,
            status_message=status,
            start_enabled=True,
            cancel_enabled=False,
            followup_visible=False,
            chatbot_history=[],
            task_description="",
        )

    @classmethod
    def reset_after_discard(cls, task_description: str) -> "MergeUIState":
        """Factory for post-discard state (preserves task description)."""
        return cls(
            merge_visible=False,
            status_message="Changes discarded",
            start_enabled=True,
            cancel_enabled=False,
            followup_visible=False,
            chatbot_history=[],
            task_description=task_description,
        )

    @classmethod
    def show_conflicts(
        cls,
        conflict_info: str,
        conflicts_html: str,
        status_message: str = "",
    ) -> "MergeUIState":
        """Factory for state with conflicts displayed."""
        return cls(
            merge_visible=True,
            conflict_visible=True,
            conflict_info=conflict_info,
            conflicts_html=conflicts_html,
            status_message=status_message,
            start_enabled=False,
            cancel_enabled=False,
            merge_header="### Conflicts to Resolve",
        )

    @classmethod
    def show_changes(
        cls,
        changes_summary: str,
        diff_content: str,
        merge_header: str = "### Changes Ready to Merge",
    ) -> "MergeUIState":
        """Factory for state with changes ready to merge."""
        return cls(
            merge_visible=True,
            changes_summary=changes_summary,
            diff_content=diff_content,
            merge_header=merge_header,
            start_enabled=False,
            cancel_enabled=False,
        )

    @classmethod
    def no_change(cls) -> "MergeUIState":
        """Factory for state that doesn't change anything (gr.update() for all)."""
        # Return a special marker that to_gradio_updates() can detect
        return cls(_no_change=True)

    _no_change: bool = field(default=False, repr=False)

    def to_gradio_updates_or_no_change(self) -> tuple:
        """Convert to Gradio updates, or return all gr.update() if _no_change is set."""
        if self._no_change:
            return tuple(gr.update() for _ in range(15))
        return self.to_gradio_updates()

    @classmethod
    def from_gradio_tuple(cls, outputs: tuple) -> "MergeUIState":
        """Parse a Gradio output tuple back into MergeUIState (for testing)."""
        if len(outputs) != 15:
            raise ValueError(f"Expected 15 elements, got {len(outputs)}")

        # Extract values from gr.update objects or raw values
        def extract_value(update, default=None):
            if isinstance(update, dict):
                return update.get("value", default)
            return update if update is not None else default

        def extract_visible(update, default=False):
            if isinstance(update, dict):
                return update.get("visible", default)
            return default

        def extract_interactive(update, default=True):
            if isinstance(update, dict):
                return update.get("interactive", default)
            return default

        return cls(
            merge_visible=extract_visible(outputs[0]),
            changes_summary=extract_value(outputs[1], ""),
            conflict_visible=extract_visible(outputs[2]),
            conflict_info=extract_value(outputs[3], ""),
            conflicts_html=extract_value(outputs[4], ""),
            status_message=extract_value(outputs[5], ""),
            chatbot_history=outputs[6] if isinstance(outputs[6], list) else [],
            start_enabled=extract_interactive(outputs[7]),
            cancel_enabled=extract_interactive(outputs[8]),
            live_stream=extract_value(outputs[9], ""),
            followup_visible=extract_visible(outputs[10]),
            task_description=extract_value(outputs[11], ""),
            merge_header=extract_value(outputs[13], ""),
            diff_content=extract_value(outputs[14], ""),
        )
