"""Backward-compat shim for quick verify runner."""

from __future__ import annotations

from chad.verification.quick_verify import main  # re-export

__all__ = ["main"]

if __name__ == "__main__":
    main()
