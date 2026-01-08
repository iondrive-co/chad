"""Backward-compat shim for verification tools."""

from __future__ import annotations

import subprocess  # Needed for tests that patch chad.tools.subprocess

from chad.verification.tools import screenshot, verify  # re-export

__all__ = ["verify", "screenshot", "subprocess"]
