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


def test_main_injects_worktree_src(monkeypatch, capsys):
    """_main should put current worktree src at the front of sys.path."""
    import sys
    from importlib import reload
    from chad.verification import visual_test_map

    visual_test_map = reload(visual_test_map)

    src_path = visual_test_map.SRC_ROOT
    assert src_path is not None and src_path.exists()

    original = list(sys.path)
    monkeypatch.setattr(sys, "path", ["placeholder", *original])
    visual_test_map._main(["--paths", "src/chad/web_ui.py"])

    assert sys.path[0] == src_path.as_posix()
