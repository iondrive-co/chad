import { useState, useCallback, useEffect } from "react";
import type { ChadAPI, DiffFull, MergeConflict } from "chad-client";
import { ChadAPIError } from "chad-client";
import { NopDiffView } from "./NopDiffView.tsx";
import { ConflictViewer } from "./ConflictViewer.tsx";

interface Props {
  api: ChadAPI;
  sessionId: string;
  onMerged: () => void;
  onDismiss: () => void;
}

type Phase = "loading" | "changes" | "merging" | "conflict" | "success" | "error";

// How often to re-poll the branch list so branches created while the panel is
// open appear in the target dropdown without reopening the panel.
const BRANCH_REFRESH_INTERVAL_MS = 5000;

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
  const [worktreeHasChanges, setWorktreeHasChanges] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshingBranches, setRefreshingBranches] = useState(false);

  // Fetch the list of branches available as merge targets. Does NOT touch the
  // selected target branch, so a periodic or manual refresh never clobbers the
  // user's current selection. Only updates `branches` when it actually changed
  // to avoid needless re-renders on the background poll.
  const refreshBranches = useCallback(async () => {
    const branchData = await api.getBranches(sessionId);
    setBranches((prev) =>
      prev.length === branchData.branches.length && prev.every((b, i) => b === branchData.branches[i])
        ? prev
        : branchData.branches,
    );
    setDefaultBranch(branchData.default);
    setCurrentBranch(branchData.current);
    return branchData;
  }, [api, sessionId]);

  // Manual refresh shows a transient "Refreshing…" label; the background poll does not.
  const handleManualRefresh = useCallback(async () => {
    setRefreshingBranches(true);
    try {
      await refreshBranches();
    } catch {
      // Ignore transient failures; the list stays as-is.
    } finally {
      setRefreshingBranches(false);
    }
  }, [refreshBranches]);

  // Initial load: branches + worktree status, and pick the default target branch.
  useEffect(() => {
    const load = async () => {
      try {
        const [branchData, worktreeStatus] = await Promise.all([
          refreshBranches(),
          api.getWorktreeStatus(sessionId),
        ]);
        setWorktreeHasChanges(worktreeStatus.has_changes);
        const preferredTarget =
          branchData.branches[0] || branchData.default || branchData.current || "";
        if (!preferredTarget) {
          setError("No branches available to merge into — the repository may have no commits yet.");
          setPhase("error");
          return;
        }
        setTargetBranch(preferredTarget);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load changes");
        setPhase("error");
      }
    };
    void load();
  }, [api, sessionId, refreshBranches]);

  // Keep the branch list fresh: branches created after the panel opened (e.g. a
  // new branch made in another tool) show up without reopening the panel.
  useEffect(() => {
    const timer = setInterval(() => {
      void refreshBranches().catch(() => {
        // Ignore transient refresh failures; the list simply stays as-is.
      });
    }, BRANCH_REFRESH_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [refreshBranches]);

  useEffect(() => {
    if (!targetBranch) {
      return;
    }

    const loadDiffSummary = async () => {
      setLoading(true);
      try {
        const summaryData = await api.getDiffSummary(sessionId, targetBranch);
        setFilesChanged(summaryData.files_changed);
        setInsertions(summaryData.insertions);
        setDeletions(summaryData.deletions);
        setDiff(null);
        setShowDiff(false);

        if (
          summaryData.files_changed === 0 &&
          summaryData.insertions === 0 &&
          summaryData.deletions === 0 &&
          !worktreeHasChanges
        ) {
          onDismiss();
          return;
        }

        setPhase("changes");
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load changes");
        setPhase("error");
      } finally {
        setLoading(false);
      }
    };

    void loadDiffSummary();
  }, [api, onDismiss, sessionId, targetBranch, worktreeHasChanges]);

  const handleViewChanges = useCallback(async () => {
    if (diff) {
      setShowDiff(!showDiff);
      return;
    }
    setLoading(true);
    try {
      const fullDiff = await api.getFullDiff(sessionId, targetBranch || null);
      setDiff(fullDiff);
      setShowDiff(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load diff");
    } finally {
      setLoading(false);
    }
  }, [api, sessionId, diff, showDiff, targetBranch]);

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
      } else if (result.conflicts && result.conflicts.length > 0) {
        setConflicts(result.conflicts);
        setPhase("conflict");
      } else {
        setError(result.message || "Merge failed");
        setPhase("error");
      }
    } catch (e) {
      if (e instanceof ChadAPIError && e.status === 404) {
        setError(
          "Session was not found on the server (it may have expired or the server restarted). " +
          "Please reopen the session and rerun the task before merging. You can discard this worktree if it is stale.",
        );
      } else {
        setError(e instanceof Error ? e.message : "Merge failed");
      }
      setPhase("error");
    }
  }, [api, sessionId, targetBranch, commitMessage]);

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
  }, [api, sessionId]);

  const handleAbortMerge = useCallback(async () => {
    setLoading(true);
    try {
      await api.abortMerge(sessionId);
      // Return to changes phase
      setConflicts([]);
      setPhase("changes");
      // Re-fetch the diff summary so files/insertions/deletions reflect the
      // state after the aborted merge instead of the pre-merge counts.
      const summaryData = await api.getDiffSummary(sessionId, targetBranch || null);
      setFilesChanged(summaryData.files_changed);
      setInsertions(summaryData.insertions);
      setDeletions(summaryData.deletions);
      setDiff(null);
      setShowDiff(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to abort merge");
    } finally {
      setLoading(false);
    }
  }, [api, sessionId, targetBranch]);

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
        <button className="merge-btn" onClick={onMerged}>Done</button>
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
  const nothingToMerge = filesChanged === 0 && insertions === 0 && deletions === 0;
  return (
    <div className="merge-panel">
      <div className="merge-header">Changes Ready to Merge</div>

      <div className="merge-summary">
        <span className="files-changed">{filesChanged} file{filesChanged !== 1 ? "s" : ""} changed</span>
        {insertions > 0 && <span className="insertions">+{insertions}</span>}
        {deletions > 0 && <span className="deletions">-{deletions}</span>}
      </div>

      {nothingToMerge && worktreeHasChanges && (
        <div className="error-text">
          These session changes are already present on "{targetBranch}", so there is nothing to
          merge into that branch. Choose a different target branch if you want to merge the same
          changes elsewhere.
        </div>
      )}

      <button
        className="expand-btn"
        onClick={handleViewChanges}
        disabled={loading}
      >
        {showDiff ? "Hide Changes" : "View Changes"}
      </button>

      {showDiff && diff && <NopDiffView files={diff.files} />}

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
          <span className="branch-label-row">
            Target Branch
            <button
              type="button"
              className="branch-refresh-btn"
              onClick={() => { void handleManualRefresh(); }}
              disabled={refreshingBranches}
              title="Refresh branch list"
            >
              {refreshingBranches ? "Refreshing…" : "↻ Refresh"}
            </button>
          </span>
          <select
            value={targetBranch}
            onChange={(e) => setTargetBranch(e.target.value)}
          >
            {/* If the branch list is empty (target fell back to the default or
                current branch), still show the selected target as an option. */}
            {(branches.length > 0 ? branches : [targetBranch]).map((b) => {
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
          disabled={phase === "merging" || loading || nothingToMerge}
          title={nothingToMerge ? "No changes to merge into the selected branch" : undefined}
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
