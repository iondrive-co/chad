"""Git worktree management for parallel task execution."""

import json
import os
import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path


def find_main_venv(project_path: Path) -> Path | None:
    """Find the main project's virtual environment directory.

    Looks for .venv or venv as actual directories (not symlinks) to avoid
    circular symlink issues when worktrees symlink to the main venv.

    Returns:
        Path to the venv directory, or None if not found.
    """
    for name in [".venv", "venv"]:
        candidate = project_path / name
        # Only use actual directories, not symlinks (to avoid circular refs)
        if candidate.exists() and candidate.is_dir() and not candidate.is_symlink():
            return candidate
    return None


def cleanup_stale_pth_entries(venv_path: Path, worktree_base: Path, current_worktree_id: str | None = None) -> int:
    """Remove stale/conflicting worktree paths from venv's .pth files.

    When worktrees share a venv via symlink, editable installs (pip install -e .)
    can pollute the venv with paths to worktrees. This causes Python to import
    from the wrong worktree, confusing agents.

    Removes entries where:
    - The worktree no longer exists (stale)
    - The worktree is different from current_worktree_id (conflicting)

    Returns number of entries removed.
    """
    removed = 0
    try:
        site_packages = list(venv_path.glob("lib/python*/site-packages"))
    except OSError:
        # Handle broken symlinks or too many symlink levels
        return 0
    if not site_packages:
        return 0

    # Match both plain paths and sys.path.insert patterns
    worktree_pattern = re.compile(
        rf"{re.escape(str(worktree_base))}/([a-f0-9]+)/src"
    )

    for sp in site_packages:
        for pth_file in sp.glob("*.pth"):
            try:
                content = pth_file.read_text()
                lines = content.splitlines()
                new_lines = []
                modified = False

                for line in lines:
                    # Check if line references a worktree src path
                    match = worktree_pattern.search(line)
                    if match:
                        worktree_id = match.group(1)
                        worktree_path = worktree_base / worktree_id
                        # Remove if worktree doesn't exist OR if it's not the current one
                        if not worktree_path.exists() or (
                            current_worktree_id and worktree_id != current_worktree_id
                        ):
                            removed += 1
                            modified = True
                            continue
                    new_lines.append(line)

                if modified:
                    pth_file.write_text("\n".join(new_lines) + "\n" if new_lines else "")
            except (OSError, PermissionError):
                continue

    return removed


@dataclass
class ConflictHunk:
    """A single conflict hunk for UI display."""

    file_path: str
    hunk_index: int
    original_lines: list[str]  # Lines from base branch (<<<<<<< HEAD)
    incoming_lines: list[str]  # Lines from worktree (>>>>>>> branch)
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)
    start_line: int = 0
    end_line: int = 0


@dataclass
class MergeConflict:
    """Represents merge conflicts in a file."""

    file_path: str
    hunks: list[ConflictHunk] = field(default_factory=list)


@dataclass
class DiffLine:
    """A single line in a diff with its type."""

    content: str
    line_type: str  # "added", "removed", "context"
    old_line_no: int | None = None
    new_line_no: int | None = None


@dataclass
class DiffHunk:
    """A hunk from a unified diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class FileDiff:
    """Diff for a single file."""

    old_path: str
    new_path: str
    hunks: list[DiffHunk] = field(default_factory=list)
    is_new: bool = False
    is_deleted: bool = False
    is_binary: bool = False


def diff_stats_from_files(files: list["FileDiff"]) -> tuple[int, int, int]:
    """Compute (files_changed, insertions, deletions) from parsed diffs."""
    insertions = 0
    deletions = 0
    for file_diff in files:
        for hunk in file_diff.hunks:
            for line in hunk.lines:
                if line.line_type == "added":
                    insertions += 1
                elif line.line_type == "removed":
                    deletions += 1
    return len(files), insertions, deletions


class GitWorktreeManager:
    """Manages git worktrees for Chad tasks."""

    WORKTREE_DIR = ".chad-worktrees"
    _repo_locks: dict[str, threading.RLock] = {}
    _repo_locks_guard = threading.Lock()

    def __init__(self, project_path: Path):
        self.project_path = Path(project_path).resolve()
        self.worktree_base = self.project_path / self.WORKTREE_DIR

    def _repo_lock(self) -> threading.RLock:
        """Get the shared lock for this repository path."""
        key = str(self.project_path)
        with self._repo_locks_guard:
            lock = self._repo_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._repo_locks[key] = lock
            return lock

    def _run_git(
        self,
        *args: str,
        cwd: Path | None = None,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        """Run a git command and return the result."""
        cmd = ["git"] + list(args)
        return subprocess.run(
            cmd,
            cwd=cwd or self.project_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=check,
            env=env,
        )

    def is_git_repo(self) -> bool:
        """Check if project_path is a git repository."""
        result = self._run_git("rev-parse", "--git-dir", check=False)
        return result.returncode == 0

    def _git_path(self, name: str) -> Path:
        """Resolve a path inside the repo's git dir (handles .git files in
        linked worktrees/submodules, where project_path/.git is not a dir)."""
        result = self._run_git("rev-parse", "--git-path", name, check=False)
        raw = result.stdout.strip()
        if not raw:
            return self.project_path / ".git" / name
        path = Path(raw)
        if not path.is_absolute():
            path = self.project_path / path
        return path

    def _ensure_worktree_dir_excluded(self) -> None:
        """Make git ignore .chad-worktrees/ via .git/info/exclude.

        Without this, the worktree base dir shows up as untracked in the main
        checkout: it pollutes `git status`, makes the pre-merge dirty check
        always fire, and could get committed by `git add -A`.
        """
        exclude_path = self._git_path("info/exclude")
        pattern = f"{self.WORKTREE_DIR}/"
        try:
            existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
            if pattern not in existing.splitlines():
                exclude_path.parent.mkdir(parents=True, exist_ok=True)
                joiner = "" if not existing or existing.endswith("\n") else "\n"
                exclude_path.write_text(existing + joiner + pattern + "\n", encoding="utf-8")
        except OSError:
            pass

    def get_main_branch(self) -> str:
        """Get the name of the main/master branch."""
        # Try common main branch names
        for name in ["main", "master"]:
            result = self._run_git("rev-parse", "--verify", name, check=False)
            if result.returncode == 0:
                return name
        # Fall back to current branch
        result = self._run_git("branch", "--show-current", check=False)
        return result.stdout.strip() or "main"

    def get_current_branch(self) -> str:
        """Get the current branch name."""
        result = self._run_git("branch", "--show-current", check=False)
        return result.stdout.strip()

    def get_branches(self) -> list[str]:
        """Get list of all local branches, with current branch first."""
        result = self._run_git("branch", "--format=%(refname:short)", check=False)
        if result.returncode != 0:
            return [self.get_main_branch()]
        branches = [b.strip() for b in result.stdout.strip().split("\n") if b.strip()]
        # Filter out chad-task branches
        branches = [b for b in branches if not b.startswith("chad-task-")]
        # Put current branch first so it's the default in dropdowns
        current = self.get_current_branch()
        if current in branches:
            branches.remove(current)
            branches.insert(0, current)
        return branches

    def _worktree_path(self, task_id: str) -> Path:
        """Get the worktree path for a task."""
        return self.worktree_base / task_id

    def _branch_name(self, task_id: str) -> str:
        """Get the branch name for a task."""
        return f"chad-task-{task_id}"

    def create_worktree(self, task_id: str) -> tuple[Path, str]:
        """Create a new worktree for a task.

        Creates branch: chad-task-{task_id}
        Creates worktree at: .chad-worktrees/{task_id}

        Returns:
            Tuple of (worktree_path, base_commit_sha)
        """
        worktree_path = self._worktree_path(task_id)
        branch_name = self._branch_name(task_id)

        # Clean up any existing worktree/branch from previous runs.
        # Also check if the branch exists even when the directory doesn't — a crash
        # can leave a stale git registration that causes "already used by worktree"
        # on the next create attempt.
        branch_registered = (
            self._run_git("rev-parse", "--verify", branch_name, check=False).returncode == 0
        )
        if self.worktree_exists(task_id) or branch_registered:
            self.delete_worktree(task_id)

        # Prune any lingering stale worktree entries (e.g. from server crashes)
        # so git doesn't block the upcoming add with "already used by worktree".
        self._run_git("worktree", "prune", check=False)

        # Ensure worktree base directory exists and is invisible to git status
        self.worktree_base.mkdir(parents=True, exist_ok=True)
        self._ensure_worktree_dir_excluded()

        # Get current commit to base the new branch on
        result = self._run_git("rev-parse", "HEAD", check=False)
        base_commit = result.stdout.strip()
        if result.returncode != 0 or not base_commit:
            raise ValueError(
                "Cannot create a worktree: the repository has no commits yet. "
                "Make an initial commit first."
            )

        # Create worktree with new branch
        worktree_env = os.environ.copy()
        worktree_env["GIT_LFS_SKIP_SMUDGE"] = "1"
        self._run_git(
            "worktree",
            "add",
            "-b",
            branch_name,
            str(worktree_path),
            base_commit,
            env=worktree_env,
        )

        # Symlink the main project's venv so agents don't need to reinstall deps
        main_venv = find_main_venv(self.project_path)
        if main_venv:
            worktree_venv = worktree_path / main_venv.name
            if not worktree_venv.exists():
                cleanup_stale_pth_entries(main_venv, self.worktree_base, current_worktree_id=task_id)
                worktree_venv.symlink_to(main_venv)

        return worktree_path, base_commit

    def worktree_exists(self, task_id: str) -> bool:
        """Check if a worktree exists for a task."""
        worktree_path = self._worktree_path(task_id)
        return worktree_path.exists()

    def delete_worktree(self, task_id: str) -> bool:
        """Delete a worktree and its associated branch."""
        worktree_path = self._worktree_path(task_id)
        branch_name = self._branch_name(task_id)

        # Remove worktree if it exists
        if worktree_path.exists():
            result = self._run_git("worktree", "remove", "--force", str(worktree_path), check=False)
            if result.returncode != 0:
                import shutil
                shutil.rmtree(worktree_path, ignore_errors=True)

        # Prune stale registrations so git won't block branch deletion with
        # "already used by worktree" (e.g. directory was deleted without cleanup).
        self._run_git("worktree", "prune", check=False)

        # Clean up any .pth files that reference this worktree
        main_venv = find_main_venv(self.project_path)
        if main_venv:
            cleanup_stale_pth_entries(main_venv, self.worktree_base)

        # Always try to delete the branch (it might exist without the worktree)
        self._run_git("branch", "-D", branch_name, check=False)

        return True

    def reset_worktree(self, task_id: str, base_commit: str | None = None) -> bool:
        """Reset a worktree to a clean state based on the provided base commit."""
        worktree_path = self._worktree_path(task_id)
        if not worktree_path.exists():
            return False

        target = base_commit or self.get_main_branch()
        self._run_git("reset", "--hard", target, cwd=worktree_path, check=False)
        self._run_git("clean", "-fd", cwd=worktree_path, check=False)
        return True

    def get_worktree_base_commit(self, task_id: str) -> str | None:
        """Infer the commit the worktree branch was originally created from."""
        branch_name = self._branch_name(task_id)
        result = self._run_git("reflog", "show", "--format=%gs", branch_name, check=False)
        if result.returncode != 0:
            return None

        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        for line in reversed(lines):
            prefix = "branch: Created from "
            if not line.startswith(prefix):
                continue
            ref = line[len(prefix):].strip()
            resolved = self._run_git("rev-parse", ref, check=False)
            commit = resolved.stdout.strip()
            if resolved.returncode == 0 and commit:
                return commit
        return None

    def has_changes(self, task_id: str, base_commit: str | None = None) -> bool:
        """Check if worktree has uncommitted changes or commits ahead of its session base."""
        worktree_path = self._worktree_path(task_id)
        if not worktree_path.exists():
            return False

        # Check for uncommitted changes
        result = self._run_git("status", "--porcelain", cwd=worktree_path, check=False)
        if result.stdout.strip():
            return True

        # Check for commits ahead of the session base (even if working tree clean)
        branch_name = self._branch_name(task_id)
        target = base_commit or self.get_worktree_base_commit(task_id) or self.get_main_branch()
        result = self._run_git("rev-list", "--count", f"{target}..{branch_name}", check=False)
        ahead_count = int(result.stdout.strip()) if result.stdout.strip() else 0
        return ahead_count > 0

    def _diff_target(
        self,
        task_id: str,
        base_commit: str | None,
        compare_branch: str | None = None,
    ) -> str:
        """Return the git ref to diff against.

        When compare_branch is provided, diff against the merge-base between that
        branch and the task worktree branch so the diff only shows worktree-side
        changes relative to the selected branch tip.
        """
        if compare_branch:
            worktree_path = self._worktree_path(task_id)
            branch_name = self._branch_name(task_id)
            result = self._run_git(
                "merge-base",
                compare_branch,
                branch_name,
                cwd=worktree_path,
                check=False,
            )
            merge_base = result.stdout.strip()
            if result.returncode != 0 or not merge_base:
                detail = result.stderr.strip() or result.stdout.strip() or compare_branch
                raise ValueError(f"Unable to compare against branch '{compare_branch}': {detail}")
            return merge_base
        if base_commit:
            return base_commit
        return self.get_main_branch()

    def get_diff_summary(
        self,
        task_id: str,
        base_commit: str | None = None,
        compare_branch: str | None = None,
    ) -> str:
        """Get a summary of all changes in the worktree vs the base."""
        worktree_path = self._worktree_path(task_id)
        if not worktree_path.exists():
            return ""

        target = self._diff_target(task_id, base_commit, compare_branch)

        # Diff working tree (committed + uncommitted) against the base
        stat_result = self._run_git("diff", "--stat", target, cwd=worktree_path, check=False)
        stat = stat_result.stdout.strip()

        # Also check for untracked files not captured by diff
        untracked = self._run_git(
            "ls-files", "--others", "--exclude-standard", cwd=worktree_path, check=False
        )
        untracked_files = [f for f in untracked.stdout.splitlines() if f.strip()]

        if not stat and not untracked_files:
            return ""

        summary_lines = ["**Changes:**"]
        if stat:
            summary_lines.append("```\n" + stat + "\n```")
        if untracked_files:
            summary_lines.append("**New files:** " + ", ".join(untracked_files))

        return "\n".join(summary_lines)

    def get_full_diff(
        self,
        task_id: str,
        base_commit: str | None = None,
        compare_branch: str | None = None,
    ) -> str:
        """Get the full diff content for all worktree changes vs the base."""
        worktree_path = self._worktree_path(task_id)
        if not worktree_path.exists():
            return ""

        target = self._diff_target(task_id, base_commit, compare_branch)
        result = self._run_git("diff", target, cwd=worktree_path, check=False)
        return result.stdout.strip() or "No changes"

    def get_diff_stats(
        self,
        task_id: str,
        base_commit: str | None = None,
        compare_branch: str | None = None,
    ) -> tuple[int, int, int]:
        """Return (files_changed, insertions, deletions) for all worktree changes.

        Computed from the parsed diff so untracked files count — `git diff
        --stat` alone ignores them, which previously made the UI report
        "0 files changed" when the only change was a newly written file.
        """
        return diff_stats_from_files(self.get_parsed_diff(task_id, base_commit, compare_branch))

    def get_parsed_diff(
        self,
        task_id: str,
        base_commit: str | None = None,
        compare_branch: str | None = None,
    ) -> list[FileDiff]:
        """Get structured diff data for all worktree changes vs the base."""
        worktree_path = self._worktree_path(task_id)
        if not worktree_path.exists():
            return []

        target = self._diff_target(task_id, base_commit, compare_branch)
        diff_texts = []

        # All changes (committed + uncommitted) vs base
        result = self._run_git("diff", target, cwd=worktree_path, check=False)
        if result.stdout.strip():
            diff_texts.append(result.stdout)

        # Untracked files - include their content as a diff against the null
        # device. The path must stay relative to the worktree so the parsed
        # FileDiff paths are repo-relative like every tracked file's.
        untracked = self._run_git("ls-files", "--others", "--exclude-standard", cwd=worktree_path, check=False)
        for path in filter(None, untracked.stdout.splitlines()):
            file_path = worktree_path / path
            if not file_path.exists():
                continue
            # git diff --no-index to generate a unified diff for new file
            new_file_diff = self._run_git(
                "diff", "--no-index", "--", os.devnull, path,
                cwd=worktree_path, check=False
            )
            if new_file_diff.stdout.strip():
                diff_texts.append(new_file_diff.stdout)

        if not diff_texts:
            return []

        return self._parse_unified_diff("\n".join(diff_texts))

    def _parse_unified_diff(self, diff_text: str) -> list[FileDiff]:
        """Parse unified diff output into structured FileDiff objects."""
        import re

        files: list[FileDiff] = []
        current_file: FileDiff | None = None
        current_hunk: DiffHunk | None = None
        old_line_no = 0
        new_line_no = 0

        for line in diff_text.split("\n"):
            # Match diff header
            if line.startswith("diff --git"):
                if current_file is not None:
                    files.append(current_file)
                # Extract paths from "diff --git a/path b/path"
                match = re.match(r"diff --git a/(.*) b/(.*)", line)
                if match:
                    current_file = FileDiff(old_path=match.group(1), new_path=match.group(2))
                    current_hunk = None
                continue

            if current_file is None:
                continue

            # Check for new/deleted file markers
            if line.startswith("new file"):
                current_file.is_new = True
            elif line.startswith("deleted file"):
                current_file.is_deleted = True
            elif line.startswith("Binary files"):
                current_file.is_binary = True

            # Match hunk header
            hunk_match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if hunk_match:
                old_start = int(hunk_match.group(1))
                old_count = int(hunk_match.group(2) or 1)
                new_start = int(hunk_match.group(3))
                new_count = int(hunk_match.group(4) or 1)

                current_hunk = DiffHunk(
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                )
                current_file.hunks.append(current_hunk)
                old_line_no = old_start
                new_line_no = new_start
                continue

            if current_hunk is None:
                continue

            # Parse diff lines
            if line.startswith("+") and not line.startswith("+++"):
                current_hunk.lines.append(
                    DiffLine(
                        content=line[1:],
                        line_type="added",
                        old_line_no=None,
                        new_line_no=new_line_no,
                    )
                )
                new_line_no += 1
            elif line.startswith("-") and not line.startswith("---"):
                current_hunk.lines.append(
                    DiffLine(
                        content=line[1:],
                        line_type="removed",
                        old_line_no=old_line_no,
                        new_line_no=None,
                    )
                )
                old_line_no += 1
            elif line.startswith(" "):
                current_hunk.lines.append(
                    DiffLine(
                        content=line[1:],
                        line_type="context",
                        old_line_no=old_line_no,
                        new_line_no=new_line_no,
                    )
                )
                old_line_no += 1
                new_line_no += 1

        if current_file is not None:
            files.append(current_file)

        return files

    def commit_all_changes(self, task_id: str, message: str = "Agent changes") -> tuple[bool, str | None]:
        """Commit all changes in the worktree and return success plus any error detail."""
        worktree_path = self._worktree_path(task_id)
        if not worktree_path.exists():
            return False, "Worktree not found"

        # Stage all changes
        add_result = self._run_git("add", "-A", cwd=worktree_path, check=False)
        if add_result.returncode != 0:
            detail = add_result.stderr.strip() or add_result.stdout.strip() or "git add failed"
            return False, detail

        # Check if there's anything to commit
        diff_result = self._run_git("diff", "--cached", "--quiet", cwd=worktree_path, check=False)
        if diff_result.returncode == 0:
            return True, None  # Nothing to commit
        if diff_result.returncode not in (0, 1):
            detail = diff_result.stderr.strip() or diff_result.stdout.strip() or "git diff failed"
            return False, detail

        # Commit
        commit_result = self._run_git("commit", "-m", message, cwd=worktree_path, check=False)
        if commit_result.returncode != 0:
            detail = commit_result.stderr.strip() or commit_result.stdout.strip() or "git commit failed"
            return False, detail

        return True, None

    def _has_main_uncommitted_changes(self) -> bool:
        """Check if the main repo has uncommitted changes to tracked files.

        Untracked files are ignored: `git stash push` (without -u) would not
        stash them anyway, and they don't block checkout/merge.
        """
        result = self._run_git("status", "--porcelain", "--untracked-files=no", check=False)
        return bool(result.stdout.strip())

    def _stash_main_changes(self) -> str:
        """Stash uncommitted tracked changes in the main repo.

        Returns the SHA of the stash commit this merge created, or "" if there
        was nothing to stash. The SHA — not the "chad-merge-stash" message — is
        what identifies the entry later: a message match also hits entries left
        behind by earlier sessions, and popping one of those replays superseded
        work onto the tree as bogus "Stashed changes" conflicts.
        """
        if not self._has_main_uncommitted_changes():
            return ""
        before = self._run_git("rev-parse", "-q", "--verify", "refs/stash", check=False).stdout.strip()
        result = self._run_git("stash", "push", "-m", "chad-merge-stash", check=False)
        if result.returncode != 0:
            return ""
        after = self._run_git("rev-parse", "-q", "--verify", "refs/stash", check=False).stdout.strip()
        return after if after and after != before else ""

    def _stash_ref_for(self, stash_sha: str) -> str | None:
        """Find the stash@{n} ref holding a given stash commit, if still present."""
        result = self._run_git("stash", "list", "--format=%gd %H", check=False)
        for line in result.stdout.splitlines():
            ref, _, sha = line.partition(" ")
            if sha.strip() == stash_sha:
                return ref
        return None

    def _restore_stash(self, stash_sha: str) -> str | None:
        """Restore the stash this merge created.

        Returns None when there was nothing to restore or it restored cleanly,
        otherwise a message describing what the user needs to deal with. A
        conflicted pop leaves the entry in `git stash list`, so nothing is lost.
        """
        if not stash_sha:
            return None
        ref = self._stash_ref_for(stash_sha)
        if ref is None:
            return None
        result = self._run_git("stash", "pop", ref, check=False)
        if result.returncode == 0:
            return None
        conflicted = self._unmerged_paths()
        if conflicted:
            return (
                "Merged, but restoring your uncommitted changes conflicted in "
                f"{', '.join(conflicted)}. Resolve the markers, then run "
                "`git stash drop` to discard the saved copy."
            )
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        return (
            f"Merged, but your uncommitted changes could not be restored ({detail}). "
            "They are still available in `git stash list`."
        )

    def merge_to_main(
        self,
        task_id: str,
        commit_message: str | None = None,
        target_branch: str | None = None,
    ) -> tuple[bool, list[MergeConflict] | None, str | None]:
        """Attempt to merge worktree changes to a target branch.

        Uses squash merge to create a single commit with the user's message,
        combining all worktree changes into one clean commit.

        Args:
            task_id: The task ID whose worktree branch to merge
            commit_message: Custom commit message for the squashed commit
            target_branch: Branch to merge into (defaults to main/master)

        Returns (success, conflicts, message) where conflicts is None on success
        or a list of MergeConflict objects on failure. message carries the reason
        for non-conflict failures (e.g. commit hooks preventing commits), and on
        success a warning if the user's stashed changes did not restore cleanly.
        """
        with self._repo_lock():
            worktree_path = self._worktree_path(task_id)
            branch_name = self._branch_name(task_id)
            merge_target = target_branch or self.get_main_branch()

            if not worktree_path.exists():
                return False, None, "Worktree not found"

            # Refuse to merge into the worktree's own branch — that would produce
            # a "already used by worktree" error from git checkout.
            if merge_target == branch_name or merge_target.startswith("chad-task-"):
                return False, None, (
                    f"Cannot merge into '{merge_target}': target must be a regular branch"
                )

            # If there is nothing to merge, surface a clear error early
            if not self.has_changes(task_id, self.get_worktree_base_commit(task_id)):
                return False, None, "No changes to merge"

            # First commit any uncommitted changes in the worktree
            # Message doesn't matter since we'll squash everything into one commit
            commit_ok, commit_error = self.commit_all_changes(task_id, "WIP")
            if not commit_ok:
                status_result = self._run_git("status", "--short", cwd=worktree_path, check=False)
                status = status_result.stdout.strip()
                detail = commit_error or status or "Failed to commit worktree changes"
                if commit_error and status:
                    detail = f"{commit_error}: {status}"
                return False, None, detail

            # Stash any uncommitted changes in main repo before checkout/merge
            stash_sha = self._stash_main_changes()

            # Switch to target branch in the main repo, remembering where the
            # user was so we can put them back afterwards.
            original_branch = self.get_current_branch()
            if original_branch and original_branch != merge_target:
                result = self._run_git("checkout", merge_target, check=False)
                if result.returncode != 0:
                    # Restore stash if checkout failed
                    self._restore_stash(stash_sha)
                    detail = result.stderr.strip() or result.stdout.strip() or "Failed to checkout target branch"
                    return False, None, detail

            # Build commit message
            final_msg = commit_message or f"Merge {branch_name}"

            # Use squash merge to combine all changes into a single commit
            result = self._run_git("merge", "--squash", branch_name, check=False)

            if result.returncode != 0:
                # Check for conflicts
                if "CONFLICT" in result.stdout or "CONFLICT" in result.stderr:
                    conflicts = self._parse_conflicts()
                    # Don't pop stash or switch branches yet — the user
                    # resolves conflicts first. Persist enough state for
                    # complete_merge/abort_merge (separate requests, separate
                    # manager instances) to restore the original branch and the
                    # stash this merge created.
                    self._save_merge_state(original_branch, stash_sha)
                    return False, conflicts, None

                # Other error - restore branch and stash
                self._restore_original_branch(original_branch)
                self._restore_stash(stash_sha)
                detail = result.stderr.strip() or result.stdout.strip() or "Merge failed"
                return False, None, detail

            # Squash merge succeeded - now commit with the user's message
            commit_result = self._run_git("commit", "-m", final_msg, check=False)
            if commit_result.returncode != 0:
                # Commit failed - abort the merge and restore state
                self._run_git("reset", "--hard", "HEAD", check=False)
                self._restore_original_branch(original_branch)
                self._restore_stash(stash_sha)
                detail = commit_result.stderr.strip() or commit_result.stdout.strip() or "Commit failed"
                return False, None, detail

            # Return to the branch the user was on, then restore their WIP
            self._restore_original_branch(original_branch)
            return True, None, self._restore_stash(stash_sha)

    # -- merge state across conflict-resolution requests ---------------------

    def _merge_state_path(self) -> Path:
        return self._git_path("chad-merge-state.json")

    def _save_merge_state(self, original_branch: str, stash_sha: str) -> None:
        """Remember the branch the user was on, and the stash we took, at merge start."""
        try:
            self._merge_state_path().write_text(
                json.dumps({"original_branch": original_branch, "stash_sha": stash_sha}),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _consume_merge_state(self) -> tuple[str, str]:
        """Read and clear the saved (original branch, stash SHA); '' for either if none."""
        path = self._merge_state_path()
        branch = ""
        stash_sha = ""
        try:
            if path.exists():
                state = json.loads(path.read_text(encoding="utf-8"))
                branch = state.get("original_branch", "")
                stash_sha = state.get("stash_sha", "")
                path.unlink()
        except (OSError, ValueError):
            pass
        return branch, stash_sha

    def _restore_original_branch(self, original_branch: str) -> None:
        """Check out the branch the user was on before the merge, if needed."""
        if not original_branch:
            return
        if self.get_current_branch() != original_branch:
            self._run_git("checkout", original_branch, check=False)

    def _parse_conflicts(self) -> list[MergeConflict]:
        """Parse conflict markers from conflicted files."""
        conflicts = []
        for file_path in self._unmerged_paths():
            full_path = self.project_path / file_path

            hunks: list[ConflictHunk] = []
            if full_path.exists():
                # errors="replace" keeps binary/mixed-encoding conflicts from
                # raising out of the merge as a 500
                content = full_path.read_text(encoding="utf-8", errors="replace")
                hunks = self._parse_conflict_hunks(file_path, content)
            # Include the file even without parseable hunks (binary conflict,
            # delete/modify, …) so the UI shows it instead of a bare
            # "Merge failed" while the repo sits mid-merge.
            conflicts.append(MergeConflict(file_path=file_path, hunks=hunks))

        return conflicts

    def _parse_conflict_hunks(self, file_path: str, content: str) -> list[ConflictHunk]:
        """Parse conflict markers from file content."""
        hunks = []
        lines = content.split("\n")
        hunk_index = 0
        i = 0

        while i < len(lines):
            if lines[i].startswith("<<<<<<<"):
                # Found conflict start
                original_lines = []
                incoming_lines = []
                start_line = i + 1

                # Collect context before (up to 3 lines)
                context_before = lines[max(0, i - 3) : i]

                i += 1
                # Collect original (HEAD) lines
                while i < len(lines) and not lines[i].startswith("======="):
                    original_lines.append(lines[i])
                    i += 1

                i += 1  # Skip =======
                # Collect incoming lines
                while i < len(lines) and not lines[i].startswith(">>>>>>>"):
                    incoming_lines.append(lines[i])
                    i += 1

                end_line = i + 1

                # Collect context after (up to 3 lines)
                context_after = lines[i + 1 : min(len(lines), i + 4)]

                hunks.append(
                    ConflictHunk(
                        file_path=file_path,
                        hunk_index=hunk_index,
                        original_lines=original_lines,
                        incoming_lines=incoming_lines,
                        context_before=context_before,
                        context_after=context_after,
                        start_line=start_line,
                        end_line=end_line,
                    )
                )
                hunk_index += 1
            i += 1

        return hunks

    def _conflict_stage_shas(self, file_path: str) -> dict[int, str]:
        """Blob SHAs for a conflicted path, keyed by stage (1=base, 2=ours, 3=theirs)."""
        result = self._run_git("ls-files", "-u", "--", file_path, check=False)
        stages: dict[int, str] = {}
        for line in result.stdout.splitlines():
            meta, _, _name = line.partition("\t")
            fields = meta.split()
            if len(fields) >= 3:
                stages[int(fields[2])] = fields[1]
        return stages

    def _write_blob(self, sha: str, dest: Path) -> None:
        """Write a blob to disk verbatim (bytes, so binary content survives)."""
        with dest.open("wb") as handle:
            subprocess.run(
                ["git", "cat-file", "blob", sha],
                cwd=self.project_path,
                stdout=handle,
                stderr=subprocess.DEVNULL,
                check=False,
            )

    def _merge_conflicted_file(self, file_path: str, use_incoming: bool) -> bool:
        """Rewrite a conflicted file, giving one side only the overlapping hunks.

        Returns False when there is nothing to merge hunk-wise — a one-sided
        (add/delete) or binary conflict, where the whole file is the only
        meaningful unit of choice.
        """
        stages = self._conflict_stage_shas(file_path)
        if 2 not in stages or 3 not in stages:
            return False

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            ours, base, theirs = tmp_dir / "ours", tmp_dir / "base", tmp_dir / "theirs"
            self._write_blob(stages[2], ours)
            self._write_blob(stages[3], theirs)
            if 1 in stages:
                self._write_blob(stages[1], base)
            else:
                base.write_bytes(b"")

            ours_bytes = ours.read_bytes()
            if b"\0" in ours_bytes or b"\0" in theirs.read_bytes():
                return False

            # merge-file keeps every hunk only one side touched and applies
            # the chosen side to the rest, writing the result into `ours`.
            side = "--theirs" if use_incoming else "--ours"
            result = self._run_git(
                "merge-file", side, str(ours), str(base), str(theirs), check=False
            )
            if result.returncode < 0:
                return False
            (self.project_path / file_path).write_bytes(ours.read_bytes())

        return True

    def resolve_all_conflicts(self, use_incoming: bool) -> bool:
        """Resolve all conflicts by favouring the original or the incoming side.

        The choice applies hunk by hunk within each file: regions only one side
        changed are kept from that side, and use_incoming decides only the
        regions that genuinely overlap. Taking the whole file with
        `git checkout --ours/--theirs` instead silently discarded every
        non-conflicting change the losing side had made elsewhere in it.
        """
        for file_path in self._unmerged_paths():
            full_path = self.project_path / file_path
            if not full_path.exists():
                continue

            if not self._merge_conflicted_file(file_path, use_incoming):
                # One-sided or binary conflict — take the whole file
                side = "--theirs" if use_incoming else "--ours"
                self._run_git("checkout", side, file_path, check=False)

            self._run_git("add", file_path, check=False)

        return True

    def _unmerged_paths(self) -> list[str]:
        """Repo-relative paths that still have conflict stages in the index."""
        result = self._run_git("diff", "--name-only", "--diff-filter=U", check=False)
        return [path for path in result.stdout.strip().split("\n") if path]

    def has_remaining_conflicts(self) -> bool:
        """Check if there are any unresolved conflicts."""
        return bool(self._unmerged_paths())

    def _is_squash_merge_in_progress(self) -> bool:
        """Check if we're in a squash merge state (not a regular merge)."""
        return self._git_path("SQUASH_MSG").exists() and not self._git_path("MERGE_HEAD").exists()

    def _is_regular_merge_in_progress(self) -> bool:
        """Check if we're in a regular merge state."""
        return self._git_path("MERGE_HEAD").exists()

    def abort_merge(self) -> bool:
        """Abort an in-progress merge (regular or squash)."""
        with self._repo_lock():
            if self._is_regular_merge_in_progress():
                # Regular merge - use git merge --abort
                result = self._run_git("merge", "--abort", check=False)
                if result.returncode != 0:
                    return False
            elif self._is_squash_merge_in_progress():
                # Squash merge - reset to HEAD and clean up SQUASH_MSG
                result = self._run_git("reset", "--hard", "HEAD", check=False)
                if result.returncode != 0:
                    return False
                squash_msg = self._git_path("SQUASH_MSG")
                if squash_msg.exists():
                    squash_msg.unlink()
            else:
                # No merge in progress
                return False
            # Put the user back on their pre-merge branch, then restore the
            # changes this merge stashed (never an earlier session's leftovers)
            original_branch, stash_sha = self._consume_merge_state()
            self._restore_original_branch(original_branch)
            self._restore_stash(stash_sha)
            return True

    def complete_merge(self, commit_message: str) -> tuple[bool, str | None]:
        """Complete the merge after all conflicts resolved.

        Args:
            commit_message: Message for the resolved merge commit. Always passed
                explicitly — letting git fall back to SQUASH_MSG produces commits
                titled "Squashed commit of the following:" with a WIP body.

        Returns (success, warning) where warning describes a stash that did not
        restore cleanly; the merge commit itself has still landed.
        """
        with self._repo_lock():
            # Refuse while unresolved conflicts remain. This check must come
            # before any staging: a blanket `git add -A` here used to stage
            # marker-laden files (hiding this guard) and sweep the user's own
            # untracked files into the merge commit.
            if self._unmerged_paths():
                return False, None  # Still have conflicts

            # Check if there's anything to commit (may be empty if conflict resolved to no changes)
            result = self._run_git("diff", "--cached", "--quiet", check=False)

            if result.returncode == 0:
                # Nothing to commit - this is OK if we resolved conflict to no net changes
                squash_msg = self._git_path("SQUASH_MSG")
                if squash_msg.exists():
                    squash_msg.unlink()
            else:
                result = self._run_git("commit", "-m", commit_message, check=False)
                if result.returncode != 0:
                    return False, None

            # Return the user to their pre-merge branch, then restore the
            # changes this merge stashed (never an earlier session's leftovers)
            original_branch, stash_sha = self._consume_merge_state()
            self._restore_original_branch(original_branch)
            return True, self._restore_stash(stash_sha)

    def cleanup_after_merge(self, task_id: str) -> bool:
        """Delete worktree and branch after successful merge."""
        return self.delete_worktree(task_id)

    def get_worktree_list(self) -> list[tuple[str, Path]]:
        """Get list of all Chad worktrees as (task_id, path) tuples."""
        result = self._run_git("worktree", "list", "--porcelain", check=False)
        if result.returncode != 0:
            return []

        worktrees = []
        current_path = None

        for line in result.stdout.split("\n"):
            if line.startswith("worktree "):
                current_path = Path(line[9:])
            elif line.startswith("branch "):
                branch = line[7:]
                if branch.startswith("refs/heads/chad-task-"):
                    task_id = branch.replace("refs/heads/chad-task-", "")
                    if current_path:
                        worktrees.append((task_id, current_path))

        return worktrees
