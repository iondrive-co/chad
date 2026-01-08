The merge function doesn't seem to be working: ❌ error: Your local changes to the following files would be overwritten by merge: src/chad/web_ui.py Please commit your changes or stash them before you merge. Aborting Merge with strategy ort failed.

->Changes failing the flake8 tests are being delivered as finished - check the recent session json logs in the temp dir to find examples. The agent should run verification (from chad.tools import verify; verify()) before completing. Check why this happens and fix it.

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
