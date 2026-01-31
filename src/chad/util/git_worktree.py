"""Git worktree management for parallel task execution."""

import re
import subprocess
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


class GitWorktreeManager:
    """Manages git worktrees for Chad tasks."""

    WORKTREE_DIR = ".chad-worktrees"

    def __init__(self, project_path: Path):
        self.project_path = Path(project_path).resolve()
        self.worktree_base = self.project_path / self.WORKTREE_DIR

    def _run_git(self, *args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
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
        )

    def is_git_repo(self) -> bool:
        """Check if project_path is a git repository."""
        result = self._run_git("rev-parse", "--git-dir", check=False)
        return result.returncode == 0

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

        # Clean up any existing worktree/branch from previous runs
        if self.worktree_exists(task_id):
            self.delete_worktree(task_id)

        # Ensure worktree base directory exists
        self.worktree_base.mkdir(parents=True, exist_ok=True)

        # Get current commit to base the new branch on
        result = self._run_git("rev-parse", "HEAD")
        base_commit = result.stdout.strip()

        # Create worktree with new branch
        self._run_git(
            "worktree",
            "add",
            "-b",
            branch_name,
            str(worktree_path),
            base_commit,
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
                # Try to prune and remove directory manually
                self._run_git("worktree", "prune", check=False)
                import shutil

                shutil.rmtree(worktree_path, ignore_errors=True)

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

    def has_changes(self, task_id: str) -> bool:
        """Check if worktree has uncommitted changes or commits ahead of main."""
        worktree_path = self._worktree_path(task_id)
        if not worktree_path.exists():
            return False

        # Check for uncommitted changes
        result = self._run_git("status", "--porcelain", cwd=worktree_path, check=False)
        if result.stdout.strip():
            return True

        # Check for commits ahead of main (even if working tree clean)
        main_branch = self.get_main_branch()
        branch_name = self._branch_name(task_id)
        result = self._run_git("rev-list", "--count", f"{main_branch}..{branch_name}", check=False)
        ahead_count = int(result.stdout.strip()) if result.stdout.strip() else 0
        return ahead_count > 0

    def get_diff_summary(self, task_id: str, base_commit: str | None = None) -> str:
        """Get a summary of *uncommitted* changes in the worktree."""
        worktree_path = self._worktree_path(task_id)
        if not worktree_path.exists():
            return ""

        # Staged + unstaged diff against HEAD
        stat_result = self._run_git("diff", "--stat", "HEAD", cwd=worktree_path, check=False)
        stat = stat_result.stdout.strip()

        status_result = self._run_git("status", "--porcelain", cwd=worktree_path, check=False)
        status = status_result.stdout.strip()

        if not status and not stat:
            return ""

        summary_lines = ["**Uncommitted changes:**"]
        if stat:
            summary_lines.append("```\n" + stat + "\n```")
        else:
            # Fall back to porcelain output for clarity when --stat is empty (e.g., rename only)
            summary_lines.append("```\n" + status + "\n```")

        return "\n".join(summary_lines)

    def get_full_diff(self, task_id: str, base_commit: str | None = None) -> str:
        """Get the full diff content for *uncommitted* worktree changes."""
        worktree_path = self._worktree_path(task_id)
        if not worktree_path.exists():
            return ""

        result = self._run_git("diff", "HEAD", cwd=worktree_path, check=False)
        return result.stdout.strip() or "No changes"

    def get_parsed_diff(self, task_id: str, base_commit: str | None = None) -> list[FileDiff]:
        """Get structured diff data for *uncommitted* worktree changes."""
        worktree_path = self._worktree_path(task_id)
        if not worktree_path.exists():
            return []

        diff_texts = []

        # Staged + unstaged changes (tracked files)
        result = self._run_git("diff", "HEAD", cwd=worktree_path, check=False)
        if result.stdout.strip():
            diff_texts.append(result.stdout)

        # Untracked files - include their content as a diff against /dev/null
        untracked = self._run_git("ls-files", "--others", "--exclude-standard", cwd=worktree_path, check=False)
        for path in filter(None, untracked.stdout.splitlines()):
            file_path = worktree_path / path
            if not file_path.exists():
                continue
            # git diff --no-index to generate a unified diff for new file
            new_file_diff = self._run_git(
                "diff", "--no-index", "--", "/dev/null", str(file_path),
                cwd=worktree_path, check=False
            )
            if new_file_diff.stdout.strip():
                diff_texts.append(new_file_diff.stdout)

        if not diff_texts:
            return []

        parsed = self._parse_unified_diff("\n".join(diff_texts))

        # Filter to files that actually appear in status/untracked to avoid showing committed-only files
        status = self._run_git("status", "--porcelain", cwd=worktree_path, check=False).stdout.splitlines()
        changed_files = {line[3:].strip() for line in status if line.strip()}
        changed_files.update(
            path for path in
            self._run_git("ls-files", "--others", "--exclude-standard", cwd=worktree_path, check=False).stdout.splitlines()
            if path.strip()
        )

        def _matches(file_path: str) -> bool:
            p = Path(file_path)
            try:
                rel = p.resolve().relative_to(worktree_path.resolve())
                rel_str = rel.as_posix()
            except Exception:
                rel_str = p.name
            return rel_str in changed_files or p.name in {Path(c).name for c in changed_files}

        return [f for f in parsed if _matches(f.new_path)]

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
        """Check if the main repo has uncommitted changes."""
        result = self._run_git("status", "--porcelain", check=False)
        return bool(result.stdout.strip())

    def _stash_main_changes(self) -> bool:
        """Stash uncommitted changes in the main repo. Returns True if stash was created."""
        if not self._has_main_uncommitted_changes():
            return False
        result = self._run_git("stash", "push", "-m", "chad-merge-stash", check=False)
        return result.returncode == 0

    def _pop_stash(self) -> tuple[bool, bool]:
        """Pop the stash. Returns (success, had_conflicts)."""
        result = self._run_git("stash", "pop", check=False)
        if result.returncode == 0:
            return True, False
        # Check if there was a conflict during stash pop
        if "CONFLICT" in result.stdout or "CONFLICT" in result.stderr:
            return False, True
        return False, False

    def _has_chad_stash(self) -> bool:
        """Check if there's a stash created by chad merge."""
        result = self._run_git("stash", "list", check=False)
        return "chad-merge-stash" in result.stdout

    def _pop_chad_stash_if_exists(self) -> None:
        """Pop the chad merge stash if it exists."""
        if self._has_chad_stash():
            self._pop_stash()

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

        Returns (success, conflicts, error_message) where conflicts is None on success
        or a list of MergeConflict objects on failure. error_message is populated for
        non-conflict failures (e.g., commit hooks preventing commits).
        """
        worktree_path = self._worktree_path(task_id)
        branch_name = self._branch_name(task_id)
        merge_target = target_branch or self.get_main_branch()

        if not worktree_path.exists():
            return False, None, "Worktree not found"

        # If there is nothing to merge, surface a clear error early
        if not self.has_changes(task_id):
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
        stashed = self._stash_main_changes()

        # Switch to target branch in the main repo
        current_branch = self.get_current_branch()
        if current_branch != merge_target:
            result = self._run_git("checkout", merge_target, check=False)
            if result.returncode != 0:
                # Restore stash if checkout failed
                if stashed:
                    self._pop_stash()
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
                # Don't pop stash yet - let user resolve merge conflicts first
                return False, conflicts, None

            # Other error - restore stash
            if stashed:
                self._pop_stash()
            detail = result.stderr.strip() or result.stdout.strip() or "Merge failed"
            return False, None, detail

        # Squash merge succeeded - now commit with the user's message
        commit_result = self._run_git("commit", "-m", final_msg, check=False)
        if commit_result.returncode != 0:
            # Commit failed - abort the merge and restore state
            self._run_git("reset", "--hard", "HEAD", check=False)
            if stashed:
                self._pop_stash()
            detail = commit_result.stderr.strip() or commit_result.stdout.strip() or "Commit failed"
            return False, None, detail

        # Pop stash after successful merge
        if stashed:
            self._pop_stash()
        return True, None, None

    def _parse_conflicts(self) -> list[MergeConflict]:
        """Parse conflict markers from conflicted files."""
        result = self._run_git("diff", "--name-only", "--diff-filter=U", check=False)
        conflicted_files = result.stdout.strip().split("\n") if result.stdout.strip() else []

        conflicts = []
        for file_path in conflicted_files:
            if not file_path:
                continue
            full_path = self.project_path / file_path
            if not full_path.exists():
                continue

            content = full_path.read_text(encoding="utf-8")
            hunks = self._parse_conflict_hunks(file_path, content)
            if hunks:
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

    def resolve_conflict(self, file_path: str, hunk_index: int, use_incoming: bool) -> bool:
        """Resolve a single conflict hunk by choosing original or incoming."""
        full_path = self.project_path / file_path
        if not full_path.exists():
            return False

        content = full_path.read_text(encoding="utf-8")
        lines = content.split("\n")
        result_lines = []
        current_hunk = 0
        i = 0

        while i < len(lines):
            if lines[i].startswith("<<<<<<<"):
                if current_hunk == hunk_index:
                    # This is the hunk to resolve
                    original_lines = []
                    incoming_lines = []

                    i += 1
                    while i < len(lines) and not lines[i].startswith("======="):
                        original_lines.append(lines[i])
                        i += 1

                    i += 1  # Skip =======
                    while i < len(lines) and not lines[i].startswith(">>>>>>>"):
                        incoming_lines.append(lines[i])
                        i += 1

                    # Add the chosen resolution
                    chosen = incoming_lines if use_incoming else original_lines
                    result_lines.extend(chosen)
                    current_hunk += 1
                else:
                    # Keep this hunk as-is (still conflicted)
                    result_lines.append(lines[i])
                    current_hunk += 1
            else:
                result_lines.append(lines[i])
            i += 1

        full_path.write_text("\n".join(result_lines))
        return True

    def resolve_all_conflicts(self, use_incoming: bool) -> bool:
        """Resolve all conflicts by choosing all original or all incoming."""
        result = self._run_git("diff", "--name-only", "--diff-filter=U", check=False)
        conflicted_files = result.stdout.strip().split("\n") if result.stdout.strip() else []

        for file_path in conflicted_files:
            if not file_path:
                continue
            full_path = self.project_path / file_path
            if not full_path.exists():
                continue

            if use_incoming:
                # Use theirs (incoming from task branch)
                self._run_git("checkout", "--theirs", file_path, check=False)
            else:
                # Use ours (main branch)
                self._run_git("checkout", "--ours", file_path, check=False)

            self._run_git("add", file_path, check=False)

        return True

    def has_remaining_conflicts(self) -> bool:
        """Check if there are any unresolved conflicts."""
        result = self._run_git("diff", "--name-only", "--diff-filter=U", check=False)
        return bool(result.stdout.strip())

    def _is_squash_merge_in_progress(self) -> bool:
        """Check if we're in a squash merge state (not a regular merge)."""
        squash_msg = self.project_path / ".git" / "SQUASH_MSG"
        merge_head = self.project_path / ".git" / "MERGE_HEAD"
        return squash_msg.exists() and not merge_head.exists()

    def _is_regular_merge_in_progress(self) -> bool:
        """Check if we're in a regular merge state."""
        merge_head = self.project_path / ".git" / "MERGE_HEAD"
        return merge_head.exists()

    def abort_merge(self) -> bool:
        """Abort an in-progress merge (regular or squash)."""
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
            squash_msg = self.project_path / ".git" / "SQUASH_MSG"
            if squash_msg.exists():
                squash_msg.unlink()
        else:
            # No merge in progress
            return False
        # Restore any stashed changes from before the merge
        self._pop_chad_stash_if_exists()
        return True

    def complete_merge(self, commit_message: str | None = None) -> bool:
        """Complete the merge after all conflicts resolved.

        Args:
            commit_message: Optional custom commit message (required for squash merge)
        """
        # Stage all resolved files
        result = self._run_git("add", "-A", check=False)
        if result.returncode != 0:
            return False

        # Check if merge is complete
        result = self._run_git("diff", "--name-only", "--diff-filter=U", check=False)
        if result.stdout.strip():
            return False  # Still have conflicts

        # Check if there's anything to commit (may be empty if conflict resolved to no changes)
        result = self._run_git("diff", "--cached", "--quiet", check=False)
        is_squash = self._is_squash_merge_in_progress()

        if result.returncode == 0:
            # Nothing to commit - this is OK if we resolved conflict to no net changes
            # Clean up the squash merge state if present
            if is_squash:
                squash_msg = self.project_path / ".git" / "SQUASH_MSG"
                if squash_msg.exists():
                    squash_msg.unlink()
            self._pop_chad_stash_if_exists()
            return True

        # Commit the merge
        if is_squash:
            # Squash merge - need to provide a message or use SQUASH_MSG
            if commit_message:
                result = self._run_git("commit", "-m", commit_message, check=False)
            else:
                # Use the default SQUASH_MSG
                result = self._run_git("commit", "--no-edit", check=False)
        else:
            # Regular merge - can use --no-edit
            result = self._run_git("commit", "--no-edit", check=False)

        if result.returncode != 0:
            return False

        # Pop any stashed changes from before the merge
        self._pop_chad_stash_if_exists()
        return True

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

    def cleanup_orphan_worktrees(self) -> list[str]:
        """Remove worktrees that no longer have active sessions."""
        # This would be called on startup to clean up from previous runs
        cleaned = []
        for task_id, path in self.get_worktree_list():
            if self.delete_worktree(task_id):
                cleaned.append(task_id)
        return cleaned
