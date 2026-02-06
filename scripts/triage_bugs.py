#!/usr/bin/env python3
"""Interactive bug triage tool.

Walks through each unverified bug in BUGS.json one by one,
prompting the user to confirm whether it is fixed or not.
- Fixed bugs are removed from BUGS.json.
- Not-fixed bugs have their status set to "unfixed".
"""

import json
import sys
import textwrap
from pathlib import Path

BUGS_FILE = Path(__file__).resolve().parent.parent / "BUGS.json"
SEPARATOR = "\u2500" * 72


def load_bugs() -> list[dict]:
    with open(BUGS_FILE) as f:
        return json.load(f)


def save_bugs(bugs: list[dict]) -> None:
    with open(BUGS_FILE, "w") as f:
        json.dump(bugs, f, indent=2)
        f.write("\n")


def print_bug(idx: int, total: int, bug: dict) -> None:
    print(f"\n{SEPARATOR}")
    print(f"  Bug {idx}/{total}  \u2014  [{bug['status'].upper()}]")
    print(SEPARATOR)
    print(f"\n  Title:       {bug['title']}")
    print(f"\n  Description: {textwrap.fill(bug['description'], width=70, initial_indent='               ', subsequent_indent='               ').strip()}")
    if bug.get("hypothesis"):
        print(f"\n  Hypothesis:  {textwrap.fill(bug['hypothesis'], width=70, initial_indent='               ', subsequent_indent='               ').strip()}")
    if bug.get("fix"):
        print(f"\n  Fix:         {textwrap.fill(bug['fix'], width=70, initial_indent='               ', subsequent_indent='               ').strip()}")
    if bug.get("files"):
        print(f"\n  Files:       {', '.join(bug['files'])}")
    print()


def prompt_verdict() -> str:
    while True:
        answer = input("  Is this bug fixed? [y/n/s(kip)/q(uit)] ").strip().lower()
        if answer in ("y", "yes"):
            return "fixed"
        if answer in ("n", "no"):
            return "not_fixed"
        if answer in ("s", "skip"):
            return "skip"
        if answer in ("q", "quit"):
            return "quit"
        print("  Please enter y (fixed), n (not fixed), s (skip), or q (quit).")


def main() -> None:
    bugs = load_bugs()
    unverified = [(i, bug) for i, bug in enumerate(bugs) if bug["status"] == "unverified"]

    if not unverified:
        print("No unverified bugs found in BUGS.json.")
        return

    print(f"\n{'=' * 72}")
    print(f"  Bug Triage  \u2014  {len(unverified)} unverified bug(s) to review")
    print(f"{'=' * 72}")

    removed_indices: list[int] = []
    marked_unfixed: list[int] = []

    for count, (orig_idx, bug) in enumerate(unverified, 1):
        print_bug(count, len(unverified), bug)
        verdict = prompt_verdict()

        if verdict == "fixed":
            removed_indices.append(orig_idx)
            print("  \u2713 Marked as FIXED \u2014 will be removed from BUGS.json")
        elif verdict == "not_fixed":
            marked_unfixed.append(orig_idx)
            print("  \u2717 Marked as UNFIXED")
        elif verdict == "skip":
            print("  \u2192 Skipped (remains unverified)")
        elif verdict == "quit":
            print("\n  Quitting early. Saving changes so far...")
            break

    # Apply changes: mark unfixed first, then remove fixed (in reverse order to preserve indices)
    for idx in marked_unfixed:
        bugs[idx]["status"] = "unfixed"

    for idx in sorted(removed_indices, reverse=True):
        del bugs[idx]

    save_bugs(bugs)

    print(f"\n{SEPARATOR}")
    print("  Summary:")
    print(f"    Removed (fixed):     {len(removed_indices)}")
    print(f"    Marked unfixed:      {len(marked_unfixed)}")
    print(f"    Remaining unverified: {sum(1 for b in bugs if b['status'] == 'unverified')}")
    print(f"    Total bugs:          {len(bugs)}")
    print(f"{SEPARATOR}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Interrupted. No changes saved.")
        sys.exit(1)
