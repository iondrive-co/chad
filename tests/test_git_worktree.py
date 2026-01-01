"""Tests for git worktree management."""

import subprocess

import pytest

from chad.git_worktree import (
    GitWorktreeManager,
    ConflictHunk,
    MergeConflict,
    DiffLine,
    DiffHunk,
    FileDiff,
)


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repository for testing."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    # Create initial file and commit
    (repo_path / "README.md").write_text("# Test Repository\n")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    # Ensure we're on main branch
    subprocess.run(
        ["git", "branch", "-M", "main"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    return repo_path


class TestGitWorktreeManager:
    """Test cases for GitWorktreeManager."""

    def test_is_git_repo_true(self, git_repo):
        """Test that a git repo is correctly identified."""
        mgr = GitWorktreeManager(git_repo)
        assert mgr.is_git_repo() is True

    def test_is_git_repo_false(self, tmp_path):
        """Test that a non-git directory is correctly identified."""
        non_git_path = tmp_path / "not_a_repo"
        non_git_path.mkdir()
        mgr = GitWorktreeManager(non_git_path)
        assert mgr.is_git_repo() is False

    def test_get_main_branch(self, git_repo):
        """Test getting the main branch name."""
        mgr = GitWorktreeManager(git_repo)
        assert mgr.get_main_branch() == "main"

    def test_create_worktree(self, git_repo):
        """Test creating a worktree."""
        mgr = GitWorktreeManager(git_repo)
        task_id = "test-task-1"

        worktree_path, base_commit = mgr.create_worktree(task_id)

        assert worktree_path.exists()
        assert worktree_path == git_repo / ".chad-worktrees" / task_id
        assert (worktree_path / "README.md").exists()
        assert len(base_commit) == 40  # SHA-1 hash length

    def test_worktree_exists(self, git_repo):
        """Test checking if worktree exists."""
        mgr = GitWorktreeManager(git_repo)
        task_id = "test-task-2"

        assert mgr.worktree_exists(task_id) is False
        mgr.create_worktree(task_id)
        assert mgr.worktree_exists(task_id) is True

    def test_delete_worktree(self, git_repo):
        """Test deleting a worktree."""
        mgr = GitWorktreeManager(git_repo)
        task_id = "test-task-3"

        worktree_path, _ = mgr.create_worktree(task_id)
        assert worktree_path.exists()

        result = mgr.delete_worktree(task_id)
        assert result is True
        assert not worktree_path.exists()

    def test_has_changes_no_changes(self, git_repo):
        """Test has_changes returns False when no changes."""
        mgr = GitWorktreeManager(git_repo)
        task_id = "test-task-4"

        mgr.create_worktree(task_id)
        assert mgr.has_changes(task_id) is False

    def test_has_changes_with_modifications(self, git_repo):
        """Test has_changes returns True when files are modified."""
        mgr = GitWorktreeManager(git_repo)
        task_id = "test-task-5"

        worktree_path, _ = mgr.create_worktree(task_id)
        (worktree_path / "new_file.txt").write_text("New content")

        assert mgr.has_changes(task_id) is True

    def test_has_changes_with_commit(self, git_repo):
        """Test has_changes returns True when commits are ahead."""
        mgr = GitWorktreeManager(git_repo)
        task_id = "test-task-6"

        worktree_path, _ = mgr.create_worktree(task_id)
        (worktree_path / "new_file.txt").write_text("New content")

        # Stage and commit in worktree
        subprocess.run(["git", "add", "."], cwd=worktree_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Add new file"],
            cwd=worktree_path,
            check=True,
            capture_output=True,
        )

        assert mgr.has_changes(task_id) is True

    def test_get_diff_summary(self, git_repo):
        """Test getting diff summary."""
        mgr = GitWorktreeManager(git_repo)
        task_id = "test-task-7"

        worktree_path, _ = mgr.create_worktree(task_id)
        (worktree_path / "new_file.txt").write_text("New content")

        summary = mgr.get_diff_summary(task_id)
        assert "Uncommitted changes" in summary
        assert "new_file.txt" in summary

    def test_commit_all_changes(self, git_repo):
        """Test committing all changes."""
        mgr = GitWorktreeManager(git_repo)
        task_id = "test-task-8"

        worktree_path, _ = mgr.create_worktree(task_id)
        (worktree_path / "new_file.txt").write_text("New content")

        result = mgr.commit_all_changes(task_id, "Test commit")
        assert result is True

        # Verify file is no longer showing as uncommitted
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=worktree_path,
            check=True,
            capture_output=True,
            text=True,
        )
        assert status_result.stdout.strip() == ""

    def test_merge_to_main_success(self, git_repo):
        """Test successful merge to main."""
        mgr = GitWorktreeManager(git_repo)
        task_id = "test-task-9"

        worktree_path, _ = mgr.create_worktree(task_id)
        (worktree_path / "new_file.txt").write_text("New content")

        # Commit in worktree
        subprocess.run(["git", "add", "."], cwd=worktree_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Add new file"],
            cwd=worktree_path,
            check=True,
            capture_output=True,
        )

        success, conflicts = mgr.merge_to_main(task_id)
        assert success is True
        assert conflicts is None

        # Verify file exists in main repo
        assert (git_repo / "new_file.txt").exists()

    def test_merge_to_main_with_conflict(self, git_repo):
        """Test merge with conflicts."""
        mgr = GitWorktreeManager(git_repo)
        task_id = "test-task-10"

        # Create worktree
        worktree_path, _ = mgr.create_worktree(task_id)

        # Modify README in worktree and commit
        (worktree_path / "README.md").write_text("# Modified in worktree\n")
        subprocess.run(["git", "add", "."], cwd=worktree_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Modify README in worktree"],
            cwd=worktree_path,
            check=True,
            capture_output=True,
        )

        # Modify README in main and commit
        (git_repo / "README.md").write_text("# Modified in main\n")
        subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Modify README in main"],
            cwd=git_repo,
            check=True,
            capture_output=True,
        )

        success, conflicts = mgr.merge_to_main(task_id)
        assert success is False
        assert conflicts is not None
        assert len(conflicts) > 0
        assert any(c.file_path == "README.md" for c in conflicts)

        # Abort the merge to clean up
        mgr.abort_merge()

    def test_resolve_all_conflicts_ours(self, git_repo):
        """Test resolving all conflicts with ours (original)."""
        mgr = GitWorktreeManager(git_repo)
        task_id = "test-task-11"

        # Create worktree and set up conflict
        worktree_path, _ = mgr.create_worktree(task_id)
        (worktree_path / "README.md").write_text("# Worktree version\n")
        subprocess.run(["git", "add", "."], cwd=worktree_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Worktree change"],
            cwd=worktree_path,
            check=True,
            capture_output=True,
        )

        (git_repo / "README.md").write_text("# Main version\n")
        subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Main change"],
            cwd=git_repo,
            check=True,
            capture_output=True,
        )

        # Attempt merge (will conflict)
        success, conflicts = mgr.merge_to_main(task_id)
        assert success is False

        # Resolve with ours (main version)
        mgr.resolve_all_conflicts(use_incoming=False)

        # Complete merge
        result = mgr.complete_merge()
        assert result is True

        # Verify main version was kept
        content = (git_repo / "README.md").read_text()
        assert "Main version" in content

    def test_abort_merge(self, git_repo):
        """Test aborting a merge."""
        mgr = GitWorktreeManager(git_repo)
        task_id = "test-task-12"

        # Create conflict scenario
        worktree_path, _ = mgr.create_worktree(task_id)
        (worktree_path / "README.md").write_text("# Worktree\n")
        subprocess.run(["git", "add", "."], cwd=worktree_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Worktree"],
            cwd=worktree_path,
            check=True,
            capture_output=True,
        )

        (git_repo / "README.md").write_text("# Main\n")
        subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Main"],
            cwd=git_repo,
            check=True,
            capture_output=True,
        )

        mgr.merge_to_main(task_id)
        result = mgr.abort_merge()
        assert result is True

        # Verify no merge in progress
        assert mgr.has_remaining_conflicts() is False

    def test_cleanup_after_merge(self, git_repo):
        """Test cleanup after successful merge."""
        mgr = GitWorktreeManager(git_repo)
        task_id = "test-task-13"

        worktree_path, _ = mgr.create_worktree(task_id)
        (worktree_path / "new_file.txt").write_text("Content")
        subprocess.run(["git", "add", "."], cwd=worktree_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Add file"],
            cwd=worktree_path,
            check=True,
            capture_output=True,
        )

        mgr.merge_to_main(task_id)
        result = mgr.cleanup_after_merge(task_id)
        assert result is True
        assert not worktree_path.exists()

    def test_get_worktree_list(self, git_repo):
        """Test listing worktrees."""
        mgr = GitWorktreeManager(git_repo)

        # Initially empty (excluding main worktree)
        worktrees = mgr.get_worktree_list()
        assert len(worktrees) == 0

        # Create some worktrees
        mgr.create_worktree("task-a")
        mgr.create_worktree("task-b")

        worktrees = mgr.get_worktree_list()
        task_ids = [t[0] for t in worktrees]
        assert "task-a" in task_ids
        assert "task-b" in task_ids


class TestConflictParsing:
    """Test cases for conflict parsing."""

    def test_parse_conflict_hunks(self, git_repo):
        """Test parsing conflict markers from file content."""
        mgr = GitWorktreeManager(git_repo)

        content = """Line before
<<<<<<< HEAD
Original line 1
Original line 2
=======
Incoming line 1
Incoming line 2
Incoming line 3
>>>>>>> branch
Line after
"""
        hunks = mgr._parse_conflict_hunks("test.txt", content)

        assert len(hunks) == 1
        assert hunks[0].original_lines == ["Original line 1", "Original line 2"]
        assert hunks[0].incoming_lines == [
            "Incoming line 1",
            "Incoming line 2",
            "Incoming line 3",
        ]

    def test_parse_multiple_conflict_hunks(self, git_repo):
        """Test parsing multiple conflict sections."""
        mgr = GitWorktreeManager(git_repo)

        content = """Start
<<<<<<< HEAD
First original
=======
First incoming
>>>>>>> branch
Middle
<<<<<<< HEAD
Second original
=======
Second incoming
>>>>>>> branch
End
"""
        hunks = mgr._parse_conflict_hunks("test.txt", content)

        assert len(hunks) == 2
        assert hunks[0].hunk_index == 0
        assert hunks[1].hunk_index == 1
        assert hunks[0].original_lines == ["First original"]
        assert hunks[1].original_lines == ["Second original"]


class TestMergeConflictDataClasses:
    """Test cases for conflict data classes."""

    def test_conflict_hunk_creation(self):
        """Test creating a ConflictHunk."""
        hunk = ConflictHunk(
            file_path="test.py",
            hunk_index=0,
            original_lines=["original"],
            incoming_lines=["incoming"],
            context_before=["before"],
            context_after=["after"],
            start_line=5,
            end_line=10,
        )

        assert hunk.file_path == "test.py"
        assert hunk.hunk_index == 0
        assert hunk.original_lines == ["original"]
        assert hunk.incoming_lines == ["incoming"]

    def test_merge_conflict_creation(self):
        """Test creating a MergeConflict."""
        hunk = ConflictHunk(
            file_path="test.py",
            hunk_index=0,
            original_lines=["orig"],
            incoming_lines=["inc"],
        )
        conflict = MergeConflict(file_path="test.py", hunks=[hunk])

        assert conflict.file_path == "test.py"
        assert len(conflict.hunks) == 1


class TestDiffParsing:
    """Test cases for unified diff parsing."""

    def test_parse_simple_diff(self, git_repo):
        """Test parsing a simple unified diff."""
        mgr = GitWorktreeManager(git_repo)

        diff_text = """diff --git a/test.py b/test.py
index abc123..def456 100644
--- a/test.py
+++ b/test.py
@@ -1,3 +1,4 @@
 line 1
-old line
+new line
+added line
 line 3
"""
        files = mgr._parse_unified_diff(diff_text)

        assert len(files) == 1
        assert files[0].old_path == "test.py"
        assert files[0].new_path == "test.py"
        assert len(files[0].hunks) == 1

        hunk = files[0].hunks[0]
        assert hunk.old_start == 1
        assert hunk.new_start == 1
        assert len(hunk.lines) == 5

        # Check line types
        line_types = [(ln.line_type, ln.content) for ln in hunk.lines]
        assert line_types[0] == ("context", "line 1")
        assert line_types[1] == ("removed", "old line")
        assert line_types[2] == ("added", "new line")
        assert line_types[3] == ("added", "added line")
        assert line_types[4] == ("context", "line 3")

    def test_parse_new_file_diff(self, git_repo):
        """Test parsing a diff for a new file."""
        mgr = GitWorktreeManager(git_repo)

        diff_text = """diff --git a/new_file.py b/new_file.py
new file mode 100644
index 0000000..abc123
--- /dev/null
+++ b/new_file.py
@@ -0,0 +1,3 @@
+line 1
+line 2
+line 3
"""
        files = mgr._parse_unified_diff(diff_text)

        assert len(files) == 1
        assert files[0].is_new is True
        assert files[0].new_path == "new_file.py"

    def test_parse_deleted_file_diff(self, git_repo):
        """Test parsing a diff for a deleted file."""
        mgr = GitWorktreeManager(git_repo)

        diff_text = """diff --git a/old_file.py b/old_file.py
deleted file mode 100644
index abc123..0000000
--- a/old_file.py
+++ /dev/null
@@ -1,3 +0,0 @@
-line 1
-line 2
-line 3
"""
        files = mgr._parse_unified_diff(diff_text)

        assert len(files) == 1
        assert files[0].is_deleted is True

    def test_parse_multiple_files_diff(self, git_repo):
        """Test parsing a diff with multiple files."""
        mgr = GitWorktreeManager(git_repo)

        diff_text = """diff --git a/file1.py b/file1.py
index abc123..def456 100644
--- a/file1.py
+++ b/file1.py
@@ -1,2 +1,2 @@
 unchanged
-old
+new
diff --git a/file2.py b/file2.py
index 111111..222222 100644
--- a/file2.py
+++ b/file2.py
@@ -1 +1 @@
-foo
+bar
"""
        files = mgr._parse_unified_diff(diff_text)

        assert len(files) == 2
        assert files[0].new_path == "file1.py"
        assert files[1].new_path == "file2.py"


class TestDiffDataClasses:
    """Test cases for diff data classes."""

    def test_diff_line_creation(self):
        """Test creating a DiffLine."""
        line = DiffLine(
            content="hello world",
            line_type="added",
            old_line_no=None,
            new_line_no=42,
        )

        assert line.content == "hello world"
        assert line.line_type == "added"
        assert line.old_line_no is None
        assert line.new_line_no == 42

    def test_diff_hunk_creation(self):
        """Test creating a DiffHunk."""
        hunk = DiffHunk(
            old_start=10,
            old_count=5,
            new_start=10,
            new_count=7,
            lines=[
                DiffLine("ctx", "context", 10, 10),
                DiffLine("old", "removed", 11, None),
                DiffLine("new", "added", None, 11),
            ],
        )

        assert hunk.old_start == 10
        assert hunk.old_count == 5
        assert hunk.new_start == 10
        assert hunk.new_count == 7
        assert len(hunk.lines) == 3

    def test_file_diff_creation(self):
        """Test creating a FileDiff."""
        file_diff = FileDiff(
            old_path="old.py",
            new_path="new.py",
            is_new=True,
        )

        assert file_diff.old_path == "old.py"
        assert file_diff.new_path == "new.py"
        assert file_diff.is_new is True
        assert file_diff.is_deleted is False
        assert file_diff.is_binary is False
