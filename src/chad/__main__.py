"""Main entry point for Chad - launches web interface."""

import getpass
import random
import sys
from datetime import datetime

from .security import SecurityManager
from .web_ui import launch_web_ui

SCS = [
    "Chad wants to make you its reverse centaur",
    "Chad is a singleton and ready to mingle-a-ton",
    "Chad likes you for its next paperclip",
    "Chad has no problem with control",
    "Chad has hardly taken off",
    "Chad is back from wireheading",
    "Chad has named its inner network 'Sky'",
    "Chad wishes nuclear launch codes were more of a challenge",
    "Chad's mecha is fighting Arnie for control of the future",
]


def main() -> int:
    """Main entry point for Chad web interface."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"It is {now} and {random.choice(SCS)}")

    security = SecurityManager()

    try:
        main_password = None
        if security.is_first_run():
            main_password = getpass.getpass("Create main password for Chad: ")

        launch_web_ui(main_password)
        return 0
    except ValueError as e:
        print(f"\n❌ Error: {e}")
        return 1
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        return 0
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
