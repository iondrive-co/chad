import type { MergeConflict, MergeConflictHunk } from "chad-client";

interface Props {
  conflicts: MergeConflict[];
}

/** Escape HTML special characters for safe rendering. */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function ConflictHunkView({ hunk }: { hunk: MergeConflictHunk }) {
  return (
    <div className="conflict-hunk">
      <div className="conflict-comparison">
        {/* Original (HEAD) side */}
        <div className="conflict-side conflict-side-ours">
          <div className="conflict-side-header ours">Original (HEAD)</div>
          <div className="conflict-side-content">
            {hunk.ours.map((line, i) => (
              <pre key={i}>{escapeHtml(line)}</pre>
            ))}
          </div>
        </div>
        {/* Incoming (worktree) side */}
        <div className="conflict-side conflict-side-theirs">
          <div className="conflict-side-header theirs">Incoming (Changes)</div>
          <div className="conflict-side-content">
            {hunk.theirs.map((line, i) => (
              <pre key={i}>{escapeHtml(line)}</pre>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function ConflictFileView({ conflict }: { conflict: MergeConflict }) {
  return (
    <div className="conflict-file">
      <div className="conflict-file-header">{conflict.file_path}</div>
      {conflict.hunks.map((hunk, i) => (
        <ConflictHunkView key={i} hunk={hunk} />
      ))}
    </div>
  );
}

export function ConflictViewer({ conflicts }: Props) {
  if (!conflicts || conflicts.length === 0) {
    return <p className="no-conflicts">No conflicts to display.</p>;
  }

  return (
    <div className="conflict-viewer">
      {conflicts.map((conflict, i) => (
        <ConflictFileView key={i} conflict={conflict} />
      ))}
    </div>
  );
}
