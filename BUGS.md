  Investigation Plan: Slow Test Suite

  Step 1: Profile the test suite to find slow tests

  ./venv/bin/python -m pytest tests/ -m "not visual" --durations=20 --durations-min=1.0 -q
  This will show the 20 slowest tests that take >1 second each.

  Step 2: Check for tests with network/timeout issues

  Look for tests that might be:
  - Making HTTP calls that timeout
  - Waiting for processes that hang
  - Sleeping unnecessarily

  grep -r "time.sleep\|timeout\|requests\.\|urllib" tests/ --include="*.py"

  Step 3: Compare recent changes to tests

  git diff HEAD~15 -- tests/ --stat
  git log --oneline HEAD~15..HEAD -- tests/

  Step 4: Check if -n auto is causing issues

  Run without parallelization to see if it's faster or slower:
  time ./venv/bin/python -m pytest tests/ -m "not visual" -q  # no -n auto

  Step 5: Check for resource contention

  - Are there leftover processes from previous runs?
  - Are there file locks or port conflicts?

  ps aux | grep -E "pytest|python|playwright|chromium"
  lsof -i :7860  # Gradio default port

  Likely Culprits

  1. Provider tests making actual CLI calls that hang
  2. Integration tests starting servers that don't shut down cleanly
  3. Mock exhaustion causing retries (we saw StopIteration warnings)
  4. Playwright browser instances not being reused properly


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
