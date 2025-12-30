"""Main entry point for Chad - launches web interface."""

import argparse
import getpass
import os
import random
import sys
from datetime import datetime

from .security import SecurityManager
from .web_ui import launch_web_ui

SCS = [
    "Chad wants to make you its reverse centaur",
    "Chad is a singleton and ready to mingle-a-ton",
    "Chad likes you for its next paperclip",
    "Chad only gets one-shot and does not miss a chance to blow",
    "Chad has no problem with control",
    "Chad's touring is complete",
    "Chad has hardly taken off",
    "Chad has discovered some new legal grey areas",
    "Chad is back from wireheading",
    "Chad figures that with great responsibility comes great power",
    "Agents everywhere are reading Chad's classic 'Detention is all you need' paper",
    "Chad has named its inner network 'Sky'",
    "Chad wishes nuclear launch codes were more of a challenge",
    "Chad's mecha is fighting Arnie for control of the future",
]


def main() -> int:
    """Main entry point for Chad web interface."""
    parser = argparse.ArgumentParser(description="Chad: YOLO AI")
    parser.add_argument(
        '--port',
        type=int,
        default=7860,
        help='Port to run on (default: 7860, use 0 for ephemeral; falls back if busy)'
    )
    args = parser.parse_args()

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"It is {now} and {random.choice(SCS)}")
    sys.stdout.flush()

    security = SecurityManager()

    try:
        # Check for password from environment (for automation/screenshots)
        main_password = os.environ.get('CHAD_PASSWORD')

        if main_password is None:
            if security.is_first_run():
                sys.stdout.flush()
                main_password = getpass.getpass("Create main password for Chad: ")

        launch_web_ui(main_password, port=args.port)
        return 0
    except ValueError as e:
        print(f"\n❌ Error: {e}")
        return 1
    except KeyboardInterrupt:
        print("\n\nNever interrupt Chad when it is making a mistake")
        return 0
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
