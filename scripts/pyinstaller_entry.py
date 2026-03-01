"""Entry point for PyInstaller builds.

PyInstaller runs the entry script as __main__ without a parent package,
so relative imports in chad/__main__.py would fail. This wrapper uses
absolute imports to bootstrap the application.
"""
import sys

from chad.__main__ import main

sys.exit(main())
