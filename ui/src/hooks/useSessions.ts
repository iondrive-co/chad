import { useState, useEffect, useCallback } from "react";
import type { ChadAPI, Session } from "chad-client";

/**
 * Hook to manage the session list.
 * Polls for updates and provides create/delete helpers.
 */
export function useSessions(api: ChadAPI | null, version: number) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!api) return;
    setLoading(true);
    try {
      const result = await api.listSessions();
      setSessions(result.sessions);
    } catch {
      // Silently handle — connection may have dropped
    } finally {
      setLoading(false);
    }
  }, [api]);

  // Refresh when api or version changes
  useEffect(() => {
    refresh();
  }, [refresh, version]);

  const createSession = useCallback(
    async (projectPath?: string) => {
      if (!api) return null;
      const session = await api.createSession({
        project_path: projectPath,
      });
      await refresh();
      return session;
    },
    [api, refresh],
  );

  const deleteSession = useCallback(
    async (id: string) => {
      if (!api) return;
      await api.deleteSession(id);
      await refresh();
    },
    [api, refresh],
  );

  return { sessions, loading, refresh, createSession, deleteSession };
}
