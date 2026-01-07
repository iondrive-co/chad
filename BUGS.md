The first task is making changes directly in the project rather than using a git worktree

The merge function doesn't seem to be working: ❌ error: Your local changes to the following files would be overwritten by merge: src/chad/web_ui.py Please commit your changes or stash them before you merge. Aborting Merge with strategy ort failed.

Changes failing the flake8 tests are being delivered as finished

There was a 5-10 minute pause in the previous coding session. The issue was that MCP tools on Windows didn't check for required dependencies before trying to use them, causing:

    Multiple Playwright browser downloads (169.8 MB Chromium + 107.1 MB Firefox)
    Repeated package installations (flake8, pytest-xdist, etc.)

These need to be shared across sessions to avoid this. Additionally a Windows session had a json error in the validation, can you fix error:

    {
      "role": "user",
      "content": "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500 \ud83d\udd0d VERIFICATION (Attempt 1) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    },
    {
      "role": "assistant",
      "content": "**VERIFICATION AI**\n\n\u274c Verification failed: No JSON found in response: *Thinking: **Ensuring valid JSON output***\n\n{\"passed\":false,\"summary\":\"Reviewed working tree changes vs agent summary and inspected new logic/tests. Found test regressions and a mismatch between repor"
    },
    {
      "role": "user",
      "content": "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500 \u274c VERIFICATION ERROR \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    }
  ],
  "verification_attempts": [
    {
      "attempt": 1,
      "status": "error",
      "feedback": "Verification failed: No JSON found in response: *Thinking: **Ensuring valid JSON output***\n\n{\"passed\":false,\"summary\":\"Reviewed working tree changes vs agent summary and inspected new logic/tests. Found test regressions and a mismatch between repor",
      "account": "codex-home"
    }
  ],

Stop live view scrolling down to bottom when new lines are added:
src/chad/visual_test_map.py
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

Roughly half the files in the src/chad directory are related to testing and verifying tools rather than the core app, can you split this into a core directory and something else. While doing this get rid of any unused code
 