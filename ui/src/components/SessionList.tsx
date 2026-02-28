import { useState, useCallback } from "react";
import type { ChadAPI, Session } from "chad-client";

interface Props {
  api: ChadAPI;
  sessions: Session[];
  loading: boolean;
  createSession: (projectPath?: string) => Promise<Session | null>;
  deleteSession: (id: string) => Promise<void>;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onRefresh: () => void;
  connected: boolean;
}

export function SessionList({
  api,
  sessions,
  loading,
  createSession,
  deleteSession,
  selectedId,
  onSelect,
  onRefresh,
  connected,
}: Props) {
  const [projectPath, setProjectPath] = useState("");
  const [creating, setCreating] = useState(false);

  const handleCreate = useCallback(async () => {
    setCreating(true);
    try {
      // Session name defaults to the session ID (set by the server)
      const session = await createSession(projectPath || undefined);
      if (session) {
        onSelect(session.id);
        onRefresh();
      }
    } finally {
      setCreating(false);
    }
  }, [createSession, projectPath, onSelect, onRefresh]);

  const handleDelete = useCallback(
    async (e: React.MouseEvent, id: string) => {
      e.stopPropagation();
      await deleteSession(id);
      onRefresh();
    },
    [deleteSession, onRefresh],
  );

  const handleResume = useCallback(
    async (e: React.MouseEvent, id: string) => {
      e.stopPropagation();
      try {
        await api.resumeSession(id);
        onRefresh();
      } catch (err) {
        console.error("Failed to resume session:", err);
      }
    },
    [api, onRefresh],
  );

  return (
    <div className="session-list">
      <div className="session-create">
        <input
          type="text"
          value={projectPath}
          onChange={(e) => setProjectPath(e.target.value)}
          placeholder="Project path (optional)"
          disabled={!connected}
        />
        <button onClick={handleCreate} disabled={creating || !connected}>
          {creating ? "..." : "New Session"}
        </button>
      </div>

      {loading && sessions.length === 0 && (
        <div className="loading">Loading...</div>
      )}

      <ul>
        {sessions.map((s) => (
          <li
            key={s.id}
            className={`session-item ${s.id === selectedId ? "selected" : ""} ${s.active ? "active" : ""}`}
            onClick={() => onSelect(s.id)}
          >
            <span className="session-name">{s.name}</span>
            {s.active && !s.paused && <span className="badge running-badge">running</span>}
            {s.paused && <span className="badge paused-badge">paused</span>}
            {s.has_changes && !s.active && <span className="badge changes-badge">changes</span>}
            {s.paused && (
              <button
                className="resume-btn"
                onClick={(e) => handleResume(e, s.id)}
                title="Resume session"
              >
                resume
              </button>
            )}
            <button
              className="delete-btn"
              onClick={(e) => handleDelete(e, s.id)}
              title="Delete session"
            >
              x
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
