from chad.verification.visual_test_map import (
    UI_COMPONENT_MAP,
    tests_for_keywords,
    tests_for_paths,
)


def test_tests_for_paths_matches_source_files():
    sample = ["src/chad/web_ui.py", "docs/readme.md"]
    tests = tests_for_paths(sample)
    # At least one web_ui entry should be returned
    assert any("TestUIElements" in t or "TestCodingAgentLayout" in t for t in tests)


def test_tests_for_keywords_finds_components():
    tests = tests_for_keywords(["task description", "provider card"])
    expected = set()
    expected.update(UI_COMPONENT_MAP["TASK_DESCRIPTION"].tests)
    expected.update(UI_COMPONENT_MAP["PROVIDER_CARD"].tests)
    assert expected.issubset(set(tests))
