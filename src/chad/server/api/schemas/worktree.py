"""Git worktree Pydantic schemas."""

from pydantic import BaseModel, Field


class WorktreeStatus(BaseModel):
    """Response model for worktree status."""

    exists: bool = Field(description="Whether the worktree exists")
    path: str | None = Field(default=None, description="Worktree directory path if exists")
    branch: str | None = Field(default=None, description="Worktree branch name if exists")
    base_commit: str | None = Field(default=None, description="Base commit SHA if exists")
    has_changes: bool = Field(default=False, description="Whether there are uncommitted changes")


class DiffLine(BaseModel):
    """A single line in a diff."""

    type: str = Field(description="Line type: 'context', 'add', 'delete', 'header'")
    content: str = Field(description="Line content")
    old_line: int | None = Field(default=None, description="Line number in old file")
    new_line: int | None = Field(default=None, description="Line number in new file")


class DiffHunk(BaseModel):
    """A hunk in a diff."""

    old_start: int = Field(description="Starting line number in old file")
    old_count: int = Field(description="Number of lines from old file")
    new_start: int = Field(description="Starting line number in new file")
    new_count: int = Field(description="Number of lines in new file")
    lines: list[DiffLine] = Field(default_factory=list, description="Lines in this hunk")


class FileDiff(BaseModel):
    """Diff for a single file."""

    old_path: str = Field(description="Path in old version")
    new_path: str = Field(description="Path in new version")
    is_new: bool = Field(default=False, description="Whether this is a new file")
    is_deleted: bool = Field(default=False, description="Whether file was deleted")
    is_binary: bool = Field(default=False, description="Whether file is binary")
    hunks: list[DiffHunk] = Field(default_factory=list, description="Diff hunks")


class DiffSummary(BaseModel):
    """Summary of changes in a diff."""

    summary: str = Field(description="Text summary of changes")
    files_changed: int = Field(description="Number of files changed")
    insertions: int = Field(description="Total lines inserted")
    deletions: int = Field(description="Total lines deleted")


class DiffFullResponse(BaseModel):
    """Response model for full diff."""

    session_id: str
    summary: DiffSummary
    files: list[FileDiff] = Field(default_factory=list, description="Per-file diffs")


class ConflictHunk(BaseModel):
    """A conflict hunk from a merge."""

    ours: list[str] = Field(default_factory=list, description="Our version of conflicted lines")
    theirs: list[str] = Field(default_factory=list, description="Their version of conflicted lines")
    base: list[str] = Field(default_factory=list, description="Base version if available")


class MergeConflict(BaseModel):
    """A merge conflict in a file."""

    file_path: str = Field(description="Path to the conflicted file")
    hunks: list[ConflictHunk] = Field(default_factory=list, description="Conflict hunks")


class MergeRequest(BaseModel):
    """Request model for merging worktree changes."""

    target_branch: str | None = Field(default=None, description="Target branch for merge")


class MergeResponse(BaseModel):
    """Response model for merge operation."""

    success: bool = Field(description="Whether merge succeeded")
    message: str = Field(description="Result message")
    conflicts: list[MergeConflict] | None = Field(default=None, description="Conflicts if merge failed")


class WorktreeResetResponse(BaseModel):
    """Response model for worktree reset."""

    session_id: str
    reset: bool = True
    message: str = "Worktree reset successfully"


class WorktreeDeleteResponse(BaseModel):
    """Response model for worktree deletion."""

    session_id: str
    deleted: bool = True
    message: str = "Worktree deleted successfully"
