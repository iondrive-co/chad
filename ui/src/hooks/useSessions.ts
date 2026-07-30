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
  // True once at least one session-list fetch has completed successfully, so
  // callers can tell an "empty list" apart from "not loaded yet".
  const [loaded, setLoaded] = useState(false);
  const apiRef = useRef(api);
  apiRef.current = api;
  // Request generation: a response only lands if no newer request has started
  // since, so a slow old poll can never overwrite fresher data.
  const requestGenRef = useRef(0);

  const refresh = useCallback(async () => {
    if (!apiRef.current) return;
    const gen = ++requestGenRef.current;
    setLoading(true);
    try {
      const result = await apiRef.current.listSessions();
      if (gen === requestGenRef.current) {
        setSessions(result.sessions);
        setLoaded(true);
      }
    } catch {
      // Silently handle — connection may have dropped
    } finally {
      if (gen === requestGenRef.current) {
        setLoading(false);
      }
    }
  }, []);

  // A different api instance means a different server — the old list is
  // meaningless there, so drop it (and invalidate in-flight requests) before
  // the first fetch against the new server.
  useEffect(() => {
    requestGenRef.current++;
    setSessions([]);
    setLoaded(false);
    setLoading(false);
  }, [api]);

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

  return { sessions, loading, loaded, refresh, createSession, deleteSession };
}
