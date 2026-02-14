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
            {s.active && <span className="badge">running</span>}
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
