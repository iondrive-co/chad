import { useState, useEffect } from "react";
import type { ChadAPI, WorktreeStatus } from "chad-client";

interface Props {
  api: ChadAPI;
  sessionId: string;
  refreshTrigger?: number;
}

export function WorktreeInfo({ api, sessionId, refreshTrigger }: Props) {
  const [status, setStatus] = useState<WorktreeStatus | null>(null);

  useEffect(() => {
    api.getWorktreeStatus(sessionId).then(setStatus).catch(() => {
      setStatus(null);
    });
  }, [api, sessionId, refreshTrigger]);

  if (!status || !status.exists) {
    return null;
  }

  return (
    <div className="worktree-info">
      <span className="worktree-label">Workspace:</span>
      <span className="worktree-path" title={status.path ?? undefined}>
        {status.path ? shortenPath(status.path) : "—"}
      </span>
      {status.branch && (
        <span className="worktree-branch">({status.branch})</span>
      )}
      {status.has_changes && (
        <span className="worktree-changes">*</span>
      )}
    </div>
  );
}

function shortenPath(path: string): string {
  const parts = path.split("/");
  if (parts.length <= 3) return path;
  return `.../${parts.slice(-2).join("/")}`;
}
