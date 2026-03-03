import { useState, useEffect, useCallback, useRef } from "react";
import type { ChadAPI, Session } from "chad-client";

const POLL_INTERVAL_MS = 3000;

/**
 * Hook to manage the session list.
 * Polls for updates and provides create/delete helpers.
 */
export function useSessions(api: ChadAPI | null, version: number) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(false);
  const apiRef = useRef(api);
  apiRef.current = api;

  const refresh = useCallback(async () => {
    if (!apiRef.current) return;
    setLoading(true);
    try {
      const result = await apiRef.current.listSessions();
      setSessions(result.sessions);
    } catch {
      // Silently handle — connection may have dropped
    } finally {
      setLoading(false);
    }
  }, []);

  // Refresh when api or version changes
  useEffect(() => {
    refresh();
  }, [refresh, api, version]);

  // Poll for session list updates so changes from other UIs are visible
  useEffect(() => {
    const timer = setInterval(() => {
      if (apiRef.current) refresh();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  const createSession = useCallback(
    async (projectPath?: string, name?: string) => {
      if (!apiRef.current) return null;
      const session = await apiRef.current.createSession({
        project_path: projectPath,
        name: name,
      });
      await refresh();
      return session;
    },
    [refresh],
  );

  const deleteSession = useCallback(
    async (id: string) => {
      if (!apiRef.current) return;
      await apiRef.current.deleteSession(id);
      await refresh();
    },
    [refresh],
  );

  return { sessions, loading, refresh, createSession, deleteSession };
}
