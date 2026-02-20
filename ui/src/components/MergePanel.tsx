import { useState, useCallback, useEffect } from "react";
import type { ChadAPI, DiffFull, MergeConflict } from "chad-client";
import { DiffViewer } from "./DiffViewer.tsx";
import { ConflictViewer } from "./ConflictViewer.tsx";

interface Props {
  api: ChadAPI;
  sessionId: string;
  onMerged: () => void;
  onDismiss: () => void;
}

type Phase = "loading" | "changes" | "merging" | "conflict" | "success" | "error";

export function MergePanel({ api, sessionId, onMerged, onDismiss }: Props) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [filesChanged, setFilesChanged] = useState(0);
  const [insertions, setInsertions] = useState(0);
  const [deletions, setDeletions] = useState(0);
  const [diff, setDiff] = useState<DiffFull | null>(null);
  const [conflicts, setConflicts] = useState<MergeConflict[]>([]);
  const [branches, setBranches] = useState<string[]>([]);
  const [defaultBranch, setDefaultBranch] = useState("main");
  const [currentBranch, setCurrentBranch] = useState("");
  const [targetBranch, setTargetBranch] = useState("");
  const [commitMessage, setCommitMessage] = useState("");
  const [showDiff, setShowDiff] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Load initial state
  useEffect(() => {
    const load = async () => {
      try {
        // Get diff summary
        const summaryData = await api.getDiffSummary(sessionId);
        setFilesChanged(summaryData.files_changed);
        setInsertions(summaryData.insertions);
        setDeletions(summaryData.deletions);

        // No actual file changes — dismiss instead of showing empty merge panel.
        // This happens when old commits exist on the branch from a previous task
        // but the current task made no changes.
        if (
          summaryData.files_changed === 0 &&
          summaryData.insertions === 0 &&
          summaryData.deletions === 0
        ) {
          onDismiss();
          return;
        }

        // Get branches
        const branchData = await api.getBranches(sessionId);
        setBranches(branchData.branches);
        setDefaultBranch(branchData.default);
        setCurrentBranch(branchData.current);
        // Default to current worktree branch, not main/default branch
        setTargetBranch(branchData.current);

        setPhase("changes");
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load changes");
        setPhase("error");
      }
    };
    load();
  }, [api, sessionId]);

  const handleViewChanges = useCallback(async () => {
    if (diff) {
      setShowDiff(!showDiff);
      return;
    }
    setLoading(true);
    try {
      const fullDiff = await api.getFullDiff(sessionId);
      setDiff(fullDiff);
      setShowDiff(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load diff");
    } finally {
      setLoading(false);
    }
  }, [api, sessionId, diff, showDiff]);

  const handleMerge = useCallback(async () => {
    setPhase("merging");
    setError(null);
    try {
      const result = await api.mergeWorktree(
        sessionId,
        targetBranch || null,
        commitMessage || null,
      );
      if (result.success) {
        setPhase("success");
        onMerged();
      } else if (result.conflicts && result.conflicts.length > 0) {
        setConflicts(result.conflicts);
        setPhase("conflict");
      } else {
        setError(result.message || "Merge failed");
        setPhase("error");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Merge failed");
      setPhase("error");
    }
  }, [api, sessionId, targetBranch, commitMessage, onMerged]);

  const handleDiscard = useCallback(async () => {
    setLoading(true);
    try {
      await api.resetWorktree(sessionId);
      onDismiss();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to discard changes");
    } finally {
      setLoading(false);
    }
  }, [api, sessionId, onDismiss]);

  const handleResolveConflicts = useCallback(async (useIncoming: boolean) => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.resolveConflicts(sessionId, useIncoming);
      if (result.success) {
        setPhase("success");
        onMerged();
      } else {
        setError(result.message || "Failed to resolve conflicts");
        setPhase("error");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to resolve conflicts");
      setPhase("error");
    } finally {
      setLoading(false);
    }
  }, [api, sessionId, onMerged]);

  const handleAbortMerge = useCallback(async () => {
    setLoading(true);
    try {
      await api.abortMerge(sessionId);
      // Return to changes phase
      setConflicts([]);
      setPhase("changes");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to abort merge");
    } finally {
      setLoading(false);
    }
  }, [api, sessionId]);

  if (phase === "loading") {
    return (
      <div className="merge-panel">
        <div className="merge-header">Loading changes...</div>
      </div>
    );
  }

  if (phase === "success") {
    return (
      <div className="merge-panel">
        <div className="merge-header success">Changes merged successfully!</div>
        <button className="merge-btn" onClick={onDismiss}>Close</button>
      </div>
    );
  }

  if (phase === "error") {
    return (
      <div className="merge-panel">
        <div className="merge-header error">Merge Error</div>
        {error && <div className="error-text">{error}</div>}
        <button onClick={onDismiss}>Close</button>
      </div>
    );
  }

  if (phase === "conflict") {
    return (
      <div className="merge-panel conflict-phase">
        <div className="merge-header">Merge Conflicts</div>
        <p className="conflict-info">
          The merge produced conflicts. Choose how to resolve them:
        </p>
        <ConflictViewer conflicts={conflicts} />
        <div className="conflict-actions">
          <button
            className="resolve-btn ours"
            onClick={() => handleResolveConflicts(false)}
            disabled={loading}
          >
            Accept All Original
          </button>
          <button
            className="resolve-btn theirs"
            onClick={() => handleResolveConflicts(true)}
            disabled={loading}
          >
            Accept All Incoming
          </button>
          <button
            className="abort-btn"
            onClick={handleAbortMerge}
            disabled={loading}
          >
            Abort Merge
          </button>
        </div>
      </div>
    );
  }

  // changes phase (or merging)
  return (
    <div className="merge-panel">
      <div className="merge-header">Changes Ready to Merge</div>

      <div className="merge-summary">
        <span className="files-changed">{filesChanged} file{filesChanged !== 1 ? "s" : ""} changed</span>
        {insertions > 0 && <span className="insertions">+{insertions}</span>}
        {deletions > 0 && <span className="deletions">-{deletions}</span>}
      </div>

      <button
        className="expand-btn"
        onClick={handleViewChanges}
        disabled={loading}
      >
        {showDiff ? "Hide Changes" : "View Changes"}
      </button>

      {showDiff && diff && <DiffViewer files={diff.files} />}

      <div className="merge-form">
        <label>
          Commit Message (optional)
          <input
            type="text"
            value={commitMessage}
            onChange={(e) => setCommitMessage(e.target.value)}
            placeholder="Describe your changes..."
          />
        </label>

        <label>
          Target Branch
          <select
            value={targetBranch}
            onChange={(e) => setTargetBranch(e.target.value)}
          >
            {branches.map((b) => {
              const labels: string[] = [];
              if (b === currentBranch) labels.push("current");
              if (b === defaultBranch) labels.push("default");
              const suffix = labels.length > 0 ? ` (${labels.join(", ")})` : "";
              return (
                <option key={b} value={b}>
                  {b}{suffix}
                </option>
              );
            })}
          </select>
        </label>
      </div>

      {error && <div className="error-text">{error}</div>}

      <div className="merge-actions">
        <button
          className="merge-btn"
          onClick={handleMerge}
          disabled={phase === "merging" || loading}
        >
          {phase === "merging" ? "Merging..." : "Accept & Merge"}
        </button>
        <button
          className="discard-btn"
          onClick={handleDiscard}
          disabled={loading}
        >
          Discard Changes
        </button>
      </div>
    </div>
  );
}
