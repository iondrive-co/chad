"""Git worktree management for parallel task execution."""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


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


class GitWorktreeManager:
    """Manages git worktrees for Chad tasks."""

    WORKTREE_DIR = ".chad-worktrees"

    def __init__(self, project_path: Path):
        self.project_path = Path(project_path).resolve()
        self.worktree_base = self.project_path / self.WORKTREE_DIR

    def _run_git(
        self, *args: str, cwd: Path | None = None, check: bool = True
    ) -> subprocess.CompletedProcess:
        """Run a git command and return the result."""
        cmd = ["git"] + list(args)
        return subprocess.run(
            cmd,
            cwd=cwd or self.project_path,
            capture_output=True,
            text=True,
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
        """Get list of all local branches."""
        result = self._run_git("branch", "--format=%(refname:short)", check=False)
        if result.returncode != 0:
            return [self.get_main_branch()]
        branches = [b.strip() for b in result.stdout.strip().split("\n") if b.strip()]
        # Filter out chad-task branches
        return [b for b in branches if not b.startswith("chad-task-")]

    def _worktree_path(self, task_id: str) -> Path:
        """Get the worktree path for a task."""
        return self.worktree_base / task_id

    def _branch_name(self, task_id: str) -> str:
        """Get the branch name for a task."""
        return f"chad-task-{task_id}"

    def create_worktree(self, task_id: str) -> Path:
        """Create a new worktree for a task.

        Creates branch: chad-task-{task_id}
        Creates worktree at: .chad-worktrees/{task_id}
        """
        worktree_path = self._worktree_path(task_id)
        branch_name = self._branch_name(task_id)

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

        return worktree_path

    def worktree_exists(self, task_id: str) -> bool:
        """Check if a worktree exists for a task."""
        worktree_path = self._worktree_path(task_id)
        return worktree_path.exists()

    def delete_worktree(self, task_id: str) -> bool:
        """Delete a worktree and its associated branch."""
        worktree_path = self._worktree_path(task_id)
        branch_name = self._branch_name(task_id)

        if not worktree_path.exists():
            return True

        # Remove worktree
        result = self._run_git("worktree", "remove", "--force", str(worktree_path), check=False)
        if result.returncode != 0:
            return False

        # Delete the branch
        self._run_git("branch", "-D", branch_name, check=False)

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

        # Check for commits ahead of main
        main_branch = self.get_main_branch()
        branch_name = self._branch_name(task_id)
        result = self._run_git(
            "rev-list", "--count", f"{main_branch}..{branch_name}", check=False
        )
        ahead_count = int(result.stdout.strip()) if result.stdout.strip() else 0
        return ahead_count > 0

    def get_diff_summary(self, task_id: str) -> str:
        """Get a summary of changes in the worktree."""
        worktree_path = self._worktree_path(task_id)
        if not worktree_path.exists():
            return ""

        main_branch = self.get_main_branch()
        branch_name = self._branch_name(task_id)

        # Get diff stat against main
        result = self._run_git(
            "diff", "--stat", f"{main_branch}...{branch_name}", cwd=worktree_path, check=False
        )
        stat = result.stdout.strip()

        # Get list of changed files
        result = self._run_git(
            "diff", "--name-status", f"{main_branch}...{branch_name}",
            cwd=worktree_path, check=False
        )
        files = result.stdout.strip()

        # Also check for uncommitted changes
        result = self._run_git("status", "--porcelain", cwd=worktree_path, check=False)
        uncommitted = result.stdout.strip()

        summary_parts = []
        if files:
            summary_parts.append(f"**Committed changes:**\n```\n{stat}\n```")
        if uncommitted:
            summary_parts.append(f"**Uncommitted changes:**\n```\n{uncommitted}\n```")

        return "\n\n".join(summary_parts) if summary_parts else "No changes detected"

    def commit_all_changes(self, task_id: str, message: str = "Agent changes") -> bool:
        """Commit all changes in the worktree."""
        worktree_path = self._worktree_path(task_id)
        if not worktree_path.exists():
            return False

        # Stage all changes
        result = self._run_git("add", "-A", cwd=worktree_path, check=False)
        if result.returncode != 0:
            return False

        # Check if there's anything to commit
        result = self._run_git("diff", "--cached", "--quiet", cwd=worktree_path, check=False)
        if result.returncode == 0:
            return True  # Nothing to commit

        # Commit
        result = self._run_git("commit", "-m", message, cwd=worktree_path, check=False)
        return result.returncode == 0

    def merge_to_main(
        self,
        task_id: str,
        commit_message: str | None = None,
        target_branch: str | None = None,
    ) -> tuple[bool, list[MergeConflict] | None]:
        """Attempt to merge worktree changes to a target branch.

        Args:
            task_id: The task ID whose worktree branch to merge
            commit_message: Custom commit message for the merge
            target_branch: Branch to merge into (defaults to main/master)

        Returns (success, conflicts) where conflicts is None on success
        or a list of MergeConflict objects on failure.
        """
        worktree_path = self._worktree_path(task_id)
        branch_name = self._branch_name(task_id)
        merge_target = target_branch or self.get_main_branch()

        if not worktree_path.exists():
            return False, None

        # First commit any uncommitted changes in the worktree
        self.commit_all_changes(task_id, "Agent changes before merge")

        # Switch to target branch in the main repo
        current_branch = self.get_current_branch()
        if current_branch != merge_target:
            result = self._run_git("checkout", merge_target, check=False)
            if result.returncode != 0:
                return False, None

        # Build merge message
        merge_msg = commit_message or f"Merge {branch_name}"

        # Attempt merge
        result = self._run_git(
            "merge", "--no-ff", branch_name, "-m", merge_msg, check=False
        )

        if result.returncode == 0:
            return True, None

        # Check for conflicts
        if "CONFLICT" in result.stdout or "CONFLICT" in result.stderr:
            conflicts = self._parse_conflicts()
            return False, conflicts

        # Other error
        return False, None

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

            content = full_path.read_text()
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

    def resolve_conflict(
        self, file_path: str, hunk_index: int, use_incoming: bool
    ) -> bool:
        """Resolve a single conflict hunk by choosing original or incoming."""
        full_path = self.project_path / file_path
        if not full_path.exists():
            return False

        content = full_path.read_text()
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

    def abort_merge(self) -> bool:
        """Abort an in-progress merge."""
        result = self._run_git("merge", "--abort", check=False)
        return result.returncode == 0

    def complete_merge(self) -> bool:
        """Complete the merge after all conflicts resolved."""
        # Stage all resolved files
        result = self._run_git("add", "-A", check=False)
        if result.returncode != 0:
            return False

        # Check if merge is complete
        result = self._run_git("diff", "--name-only", "--diff-filter=U", check=False)
        if result.stdout.strip():
            return False  # Still have conflicts

        # Commit the merge
        result = self._run_git("commit", "--no-edit", check=False)
        return result.returncode == 0

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
