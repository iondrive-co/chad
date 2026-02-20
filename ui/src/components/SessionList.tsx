import { useState, useCallback } from "react";
import type { ChadAPI } from "chad-client";
import { useSessions } from "../hooks/useSessions.ts";

interface Props {
  api: ChadAPI;
  selectedId: string | null;
  onSelect: (id: string) => void;
  version: number;
  onRefresh: () => void;
}

export function SessionList({
  api,
  selectedId,
  onSelect,
  version,
  onRefresh,
}: Props) {
  const { sessions, loading, createSession, deleteSession } = useSessions(
    api,
    version,
  );
  const [projectPath, setProjectPath] = useState("");
  const [creating, setCreating] = useState(false);

  const handleCreate = useCallback(async () => {
    setCreating(true);
    try {
      // Compute next task number from existing sessions
      const taskNumbers = sessions
        .map((s) => {
          const match = s.name.match(/^Task (\d+)$/);
          return match ? parseInt(match[1], 10) : 0;
        })
        .filter((n) => n > 0);
      const nextNumber = taskNumbers.length > 0 ? Math.max(...taskNumbers) + 1 : 1;
      const taskName = `Task ${nextNumber}`;

      const session = await createSession(projectPath || undefined, taskName);
      if (session) {
        onSelect(session.id);
        onRefresh();
      }
    } finally {
      setCreating(false);
    }
  }, [createSession, projectPath, sessions, onSelect, onRefresh]);

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
        />
        <button onClick={handleCreate} disabled={creating}>
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
