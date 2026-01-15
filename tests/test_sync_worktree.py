from __future__ import annotations


def test_sync_paths_copies_files_without_rsync(monkeypatch, tmp_path):
    import chad.verification.sync_worktree as sync_worktree

    source = tmp_path / "src"
    dest = tmp_path / "dest"
    source.mkdir()
    dest.mkdir()
    (source / "file.txt").write_text("hello")
    (source / ".git").mkdir()
    (source / ".git" / "ignored.txt").write_text("ignore me")

    monkeypatch.setattr(sync_worktree, "_rsync_available", lambda: False)

    result = sync_worktree.sync_paths(source, dest, prefer_rsync=True)

    assert result.method == "python"
    assert (dest / "file.txt").read_text() == "hello"
    assert not (dest / ".git").exists()


def test_sync_paths_delete_removes_extra(monkeypatch, tmp_path):
    import chad.verification.sync_worktree as sync_worktree

    source = tmp_path / "src2"
    dest = tmp_path / "dest2"
    source.mkdir()
    dest.mkdir()
    (source / "keep.txt").write_text("stay")
    (dest / "keep.txt").write_text("old")
    (dest / "stale.txt").write_text("remove me")

    monkeypatch.setattr(sync_worktree, "_rsync_available", lambda: False)

    result = sync_worktree.sync_paths(source, dest, delete=True, prefer_rsync=True)

    assert result.deleted == 1
    assert (dest / "keep.txt").read_text() == "stay"
    assert not (dest / "stale.txt").exists()
