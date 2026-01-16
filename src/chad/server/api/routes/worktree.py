"""Git worktree management endpoints."""

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException

from chad.util.git_worktree import GitWorktreeManager
from chad.server.api.schemas import (
    WorktreeStatus,
    DiffSummary,
    DiffFullResponse,
    DiffLine as SchemaDiffLine,
    DiffHunk as SchemaDiffHunk,
    FileDiff as SchemaFileDiff,
    MergeRequest,
    MergeResponse,
    MergeConflict as SchemaMergeConflict,
    ConflictHunk as SchemaConflictHunk,
    WorktreeResetResponse,
    WorktreeDeleteResponse,
)
from chad.server.services import get_session_manager

router = APIRouter()


def _get_session_or_404(session_id: str):
    """Get a session by ID or raise 404."""
    manager = get_session_manager()
    session = manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return session


def _get_worktree_manager(session) -> GitWorktreeManager:
    """Get GitWorktreeManager for a session's project path."""
    if not session.project_path:
        raise HTTPException(status_code=400, detail="Session has no project path set")
    project_path = Path(session.project_path)
    if not project_path.exists():
        raise HTTPException(status_code=400, detail=f"Project path does not exist: {session.project_path}")
    return GitWorktreeManager(project_path)


def _parse_diff_stats(diff_summary: str) -> tuple[int, int, int]:
    """Parse diff summary to extract file count, insertions, deletions."""
    files_changed = 0
    insertions = 0
    deletions = 0

    # Match lines like "3 files changed, 10 insertions(+), 5 deletions(-)"
    for line in diff_summary.split("\n"):
        if "file" in line and "changed" in line:
            match = re.search(r"(\d+) files? changed", line)
            if match:
                files_changed = int(match.group(1))
            match = re.search(r"(\d+) insertions?\(\+\)", line)
            if match:
                insertions = int(match.group(1))
            match = re.search(r"(\d+) deletions?\(-\)", line)
            if match:
                deletions = int(match.group(1))
            break

    return files_changed, insertions, deletions


@router.post("/{session_id}/worktree", response_model=WorktreeStatus, status_code=201)
async def create_worktree(session_id: str) -> WorktreeStatus:
    """Create a git worktree for a session.

    Creates an isolated git worktree for the session's task to work in.
    Changes can later be merged back to the main branch.
    """
    session = _get_session_or_404(session_id)
    wt_mgr = _get_worktree_manager(session)

    if not wt_mgr.is_git_repo():
        raise HTTPException(status_code=400, detail="Project is not a git repository")

    # Use session ID as task ID
    worktree_path, base_commit = wt_mgr.create_worktree(session_id)

    # Update session state
    session.worktree_path = worktree_path
    session.worktree_branch = wt_mgr._branch_name(session_id)
    session.worktree_base_commit = base_commit

    return WorktreeStatus(
        exists=True,
        path=str(worktree_path),
        branch=session.worktree_branch,
        base_commit=base_commit,
        has_changes=False,
    )


@router.get("/{session_id}/worktree", response_model=WorktreeStatus)
async def get_worktree_status(session_id: str) -> WorktreeStatus:
    """Get the worktree status for a session."""
    session = _get_session_or_404(session_id)

    if not session.worktree_path:
        return WorktreeStatus(exists=False)

    wt_mgr = _get_worktree_manager(session)
    exists = wt_mgr.worktree_exists(session_id)

    if not exists:
        return WorktreeStatus(exists=False)

    has_changes = wt_mgr.has_changes(session_id)
    session.has_worktree_changes = has_changes

    return WorktreeStatus(
        exists=True,
        path=str(session.worktree_path),
        branch=session.worktree_branch,
        base_commit=session.worktree_base_commit,
        has_changes=has_changes,
    )


@router.get("/{session_id}/worktree/diff", response_model=DiffSummary)
async def get_diff_summary(session_id: str) -> DiffSummary:
    """Get a summary of changes in the worktree."""
    session = _get_session_or_404(session_id)

    if not session.worktree_path:
        raise HTTPException(status_code=400, detail="Session has no worktree")

    wt_mgr = _get_worktree_manager(session)
    if not wt_mgr.worktree_exists(session_id):
        raise HTTPException(status_code=400, detail="Worktree does not exist")

    summary_text = wt_mgr.get_diff_summary(session_id, session.worktree_base_commit)
    files_changed, insertions, deletions = _parse_diff_stats(summary_text)

    return DiffSummary(
        summary=summary_text,
        files_changed=files_changed,
        insertions=insertions,
        deletions=deletions,
    )


@router.get("/{session_id}/worktree/diff/full", response_model=DiffFullResponse)
async def get_full_diff(session_id: str) -> DiffFullResponse:
    """Get the full diff with file-by-file details."""
    session = _get_session_or_404(session_id)

    if not session.worktree_path:
        raise HTTPException(status_code=400, detail="Session has no worktree")

    wt_mgr = _get_worktree_manager(session)
    if not wt_mgr.worktree_exists(session_id):
        raise HTTPException(status_code=400, detail="Worktree does not exist")

    # Get summary
    summary_text = wt_mgr.get_diff_summary(session_id, session.worktree_base_commit)
    files_changed, insertions, deletions = _parse_diff_stats(summary_text)
    summary = DiffSummary(
        summary=summary_text,
        files_changed=files_changed,
        insertions=insertions,
        deletions=deletions,
    )

    # Get parsed diff
    parsed_files = wt_mgr.get_parsed_diff(session_id, session.worktree_base_commit)

    # Convert to schema types
    schema_files = []
    for file_diff in parsed_files:
        schema_hunks = []
        for hunk in file_diff.hunks:
            schema_lines = [
                SchemaDiffLine(
                    type=line.line_type.replace("removed", "delete").replace("added", "add"),
                    content=line.content,
                    old_line=line.old_line_no,
                    new_line=line.new_line_no,
                )
                for line in hunk.lines
            ]
            schema_hunks.append(
                SchemaDiffHunk(
                    old_start=hunk.old_start,
                    old_count=hunk.old_count,
                    new_start=hunk.new_start,
                    new_count=hunk.new_count,
                    lines=schema_lines,
                )
            )
        schema_files.append(
            SchemaFileDiff(
                old_path=file_diff.old_path,
                new_path=file_diff.new_path,
                is_new=file_diff.is_new,
                is_deleted=file_diff.is_deleted,
                is_binary=file_diff.is_binary,
                hunks=schema_hunks,
            )
        )

    return DiffFullResponse(
        session_id=session_id,
        summary=summary,
        files=schema_files,
    )


@router.post("/{session_id}/worktree/merge", response_model=MergeResponse)
async def merge_worktree(session_id: str, request: MergeRequest) -> MergeResponse:
    """Merge worktree changes back to the main branch.

    Returns success/failure and any merge conflicts.
    """
    session = _get_session_or_404(session_id)

    if not session.worktree_path:
        raise HTTPException(status_code=400, detail="Session has no worktree")

    wt_mgr = _get_worktree_manager(session)
    if not wt_mgr.worktree_exists(session_id):
        raise HTTPException(status_code=400, detail="Worktree does not exist")

    success, conflicts, error_msg = wt_mgr.merge_to_main(
        session_id,
        target_branch=request.target_branch,
    )

    if success:
        return MergeResponse(
            success=True,
            message="Changes merged successfully",
            conflicts=None,
        )

    if conflicts:
        # Convert to schema types
        schema_conflicts = []
        for conflict in conflicts:
            schema_hunks = [
                SchemaConflictHunk(
                    ours=hunk.original_lines,
                    theirs=hunk.incoming_lines,
                    base=[],
                )
                for hunk in conflict.hunks
            ]
            schema_conflicts.append(
                SchemaMergeConflict(
                    file_path=conflict.file_path,
                    hunks=schema_hunks,
                )
            )
        session.merge_conflicts = conflicts
        return MergeResponse(
            success=False,
            message="Merge has conflicts that need resolution",
            conflicts=schema_conflicts,
        )

    return MergeResponse(
        success=False,
        message=error_msg or "Merge failed",
        conflicts=None,
    )


@router.post("/{session_id}/worktree/reset", response_model=WorktreeResetResponse)
async def reset_worktree(session_id: str) -> WorktreeResetResponse:
    """Reset the worktree to its original state.

    Discards all changes made in the worktree.
    """
    session = _get_session_or_404(session_id)

    if not session.worktree_path:
        raise HTTPException(status_code=400, detail="Session has no worktree")

    wt_mgr = _get_worktree_manager(session)
    if not wt_mgr.worktree_exists(session_id):
        raise HTTPException(status_code=400, detail="Worktree does not exist")

    success = wt_mgr.reset_worktree(session_id, session.worktree_base_commit)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to reset worktree")

    session.has_worktree_changes = False

    return WorktreeResetResponse(
        session_id=session_id,
        reset=True,
        message="Worktree reset successfully",
    )


@router.delete("/{session_id}/worktree", response_model=WorktreeDeleteResponse)
async def delete_worktree(session_id: str) -> WorktreeDeleteResponse:
    """Delete the worktree for a session."""
    session = _get_session_or_404(session_id)

    if not session.worktree_path:
        raise HTTPException(status_code=400, detail="Session has no worktree")

    wt_mgr = _get_worktree_manager(session)
    wt_mgr.delete_worktree(session_id)

    # Clear session worktree state
    session.worktree_path = None
    session.worktree_branch = None
    session.worktree_base_commit = None
    session.has_worktree_changes = False
    session.merge_conflicts = None

    return WorktreeDeleteResponse(
        session_id=session_id,
        deleted=True,
        message="Worktree deleted successfully",
    )
