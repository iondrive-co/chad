"""Provider integration tests.

These tests run against REAL provider CLIs (Claude Code, Codex, etc.) and use
actual API tokens. They are NOT part of the normal test suite.

Purpose:
--------
Catch regressions that unit tests can't detect because they mock providers.
Examples:
- CLI interpreting certain output as completion signals
- Stdin/stdout handling differences between providers
- Prompt format compatibility issues

Running:
--------
# Run all provider integration tests (requires tokens)
CHAD_RUN_PROVIDER_TESTS=1 pytest tests/provider_integration/ -v

# Run for a specific provider
CHAD_RUN_PROVIDER_TESTS=1 pytest tests/provider_integration/ -v -k codex

Environment:
------------
- CHAD_RUN_PROVIDER_TESTS=1: Required to run these tests
- Provider-specific: Uses accounts configured in ~/.chad.conf

Cost Warning:
-------------
These tests consume real API tokens. Run sparingly (before releases, after
major changes to prompts/streaming/PTY code).
"""
