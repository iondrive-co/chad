The merge function doesn't seem to be working: ❌ error: Your local changes to the following files would be overwritten by merge: src/chad/web_ui.py Please commit your changes or stash them before you merge. Aborting Merge with strategy ort failed.

When adding a new provider after completing login flow it shows as a grey box until the refresh button is pressed. This refresh should happen automatically.
Also it does not show up as a new coding agent in that dropdown until the app is restarted

Stop live view scrolling down to bottom when new lines are added, as a starting point consider:
src/chad/verification/visual_test_map.py
Original
        "TestSubtaskTabs",
        "TestLiveViewFormat",
        "TestRealisticLiveContent",
        "TestNoStatusBox",
        "TestScreenshots",
        "TestProviderTwoColumnLayout",
Modified
        "TestSubtaskTabs",
        "TestLiveViewFormat",
        "TestRealisticLiveContent",
        "TestLiveViewScrollBehavior",
        "TestNoStatusBox",
        "TestScreenshots",
        "TestProviderTwoColumnLayout",
src/chad/web_ui.py
Original
        if (!container || !state) return;
        state.ignoreNextScroll = true;
        requestAnimationFrame(() => {
            // null = user hasn't scrolled, use container's current position (allow auto-scroll)
            // number = user scrolled to that position, restore it (including 0 for top)
            const targetScrollTop =
                state.savedScrollTop !== null ? state.savedScrollTop : container.scrollTop;
            container.scrollTop = targetScrollTop;
            setTimeout(() => { state.ignoreNextScroll = false; }, 100);
        });
    }
Modified
        if (!container || !state) return;
        state.ignoreNextScroll = true;
        requestAnimationFrame(() => {
            // If user has scrolled away from bottom, maintain their position
            if (state.userScrolledUp && state.savedScrollTop !== null) {
                container.scrollTop = state.savedScrollTop;
            }
            // Otherwise, if user is at bottom or hasn't scrolled, scroll to bottom for new content
            else if (!state.userScrolledUp) {
                container.scrollTop = container.scrollHeight;
            }
            setTimeout(() => { state.ignoreNextScroll = false; }, 100);
        });
    }

Once a task is discarded the task description should be made editable again

Get rid of status line which is wrong and doesn't update: "✓ Ready — Coding: codex-home (openai, gpt-5.2-codex)"
