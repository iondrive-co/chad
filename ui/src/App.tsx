import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { ChadAPI } from "chad-client";
import type { ProjectSettings } from "chad-client";
import { ChatView } from "./components/ChatView.tsx";
import { SettingsPanel } from "./components/SettingsPanel.tsx";
import { ProvidersPanel } from "./components/ProvidersPanel.tsx";
import { ProjectsPanel } from "./components/ProjectsPanel.tsx";
import { useSessions } from "./hooks/useSessions.ts";

type Tab = "chat" | "projects" | "providers" | "settings";

/**
 * Parse a connection input string into an API base URL and optional auth token.
 * Handles:
 *   - Direct URLs: "http://localhost:8000", "https://my.server.com"
 *   - Host:port shorthand: "localhost:8000", "192.168.1.5:3000"
 *   - CF tunnel with token: "subdomain:mytoken" (non-numeric suffix)
 *   - CF tunnel subdomain only: "my-tunnel"
 */
function parseConnectionInput(input: string): { url: string; token?: string } {
  const text = input.trim().replace(/\/+$/, "");
  if (!text) return { url: "" };

  // Direct URL with protocol
  if (text.includes("://")) {
    return { url: text };
  }

  // host:port — digits-only after the last colon
  const colonIdx = text.lastIndexOf(":");
  if (colonIdx > 0) {
    const afterColon = text.slice(colonIdx + 1);
    if (/^\d+$/.test(afterColon)) {
      return { url: `http://${text}` };
    }
    // CF tunnel "subdomain:token"
    const subdomain = text.slice(0, colonIdx);
    return { url: `https://${subdomain}.trycloudflare.com`, token: afterColon };
  }

  // Bare string — CF tunnel subdomain
  return { url: `https://${text}.trycloudflare.com` };
}

export { parseConnectionInput };

const DEFAULT_CONNECTION = "127.0.0.1:3184";

export function App() {
  const [apiBaseUrl, setApiBaseUrl] = useState("");
  const [connectionInput, setConnectionInput] = useState(DEFAULT_CONNECTION);
  const [token, setToken] = useState<string | undefined>(undefined);
  const api = useMemo(() => new ChadAPI(apiBaseUrl, token), [apiBaseUrl, token]);
  const [connected, setConnected] = useState(false);
  const [selectedSession, setSelectedSession] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("projects");
  const [sessionVersion, setSessionVersion] = useState(0);
  // Track the project selected for the current session (set when opening from ProjectsPanel)
  const [sessionProjectPath, setSessionProjectPath] = useState("");
  // All configured projects, loaded on connect
  const [projects, setProjects] = useState<ProjectSettings[]>([]);
  // Track which sessions have been opened in this UI instance (only these show as tabs)
  const [openedSessionIds, setOpenedSessionIds] = useState<Set<string>>(new Set());
  // Track whether the user has ever set a URL (vs initial empty state)
  const hasUrl = useRef(false);

  // Load projects when connected
  const loadProjects = useCallback(async () => {
    if (!connected) return;
    try {
      const result = await api.listProjects();
      setProjects(result);
    } catch {
      // ignore
    }
  }, [api, connected]);

  // Auto-connect when apiBaseUrl changes, retry only if we have a URL
  useEffect(() => {
    if (!apiBaseUrl) {
      // No URL set yet — stay disconnected, don't retry
      setConnected(false);

      return;
    }
    hasUrl.current = true;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    setConnected(false);
    const tryConnect = async () => {
      try {
        await api.getStatus();
        if (!cancelled) {
          setConnected(true);
        }
      } catch {
        if (!cancelled) {
          timer = setTimeout(tryConnect, 1000);
        }
      }
    };
    tryConnect();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [api]);

  // Load projects once connected
  useEffect(() => {
    if (connected) loadProjects();
  }, [connected, loadProjects]);

  const { sessions, loading: sessionsLoading, createSession, deleteSession } = useSessions(
    connected ? api : null,
    sessionVersion,
  );

  // Get selected session's active state from polled data
  const selectedSessionActive = sessions.find(s => s.id === selectedSession)?.active ?? false;

  const refreshSessions = useCallback(() => {
    setSessionVersion((v) => v + 1);
  }, []);

  // On initial mount, check for #pair=... hash (from QR code scan) or
  // auto-detect if we're served by the API (not file://)
  useEffect(() => {
    const hash = window.location.hash;
    const pairMatch = hash.match(/^#pair=(.+)$/);
    if (pairMatch) {
      const parsed = parseConnectionInput(pairMatch[1]);
      if (parsed.url) {
        setApiBaseUrl(parsed.url);
        setToken(parsed.token);
        setConnectionInput(pairMatch[1]);
      }
      history.replaceState(null, "", window.location.pathname + window.location.search);
    } else if (window.location.protocol !== "file:" && !hasUrl.current && window.self === window.top) {
      setApiBaseUrl(window.location.origin);
    }
  }, []);

  const handleNewSession = useCallback(async (projectPath?: string) => {
    // Default to first configured project when none specified
    const effectivePath = projectPath || (projects.length > 0 ? projects[0].project_path : undefined);
    const session = await createSession(effectivePath);
    if (session) {
      setSelectedSession(session.id);
      setOpenedSessionIds(prev => new Set(prev).add(session.id));
      if (effectivePath) setSessionProjectPath(effectivePath);
      setTab("chat");
      refreshSessions();
    }
  }, [createSession, refreshSessions, projects]);

  const handleDeleteSession = useCallback(async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    await deleteSession(id);
    setOpenedSessionIds(prev => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
    if (selectedSession === id) {
      // Select another opened session, or deselect
      const remaining = sessions.filter(s => s.id !== id && openedSessionIds.has(s.id));
      setSelectedSession(remaining[0]?.id ?? null);
    }
    refreshSessions();
  }, [deleteSession, selectedSession, sessions, openedSessionIds, refreshSessions]);

  const handleSelectSession = useCallback((id: string) => {
    setSelectedSession(id);
    const session = sessions.find(s => s.id === id);
    if (session?.project_path) setSessionProjectPath(session.project_path);
    setTab("chat");
  }, [sessions]);

  const handleOpenSessionFromProject = useCallback((sessionId: string, projectPath: string) => {
    setSelectedSession(sessionId);
    setOpenedSessionIds(prev => new Set(prev).add(sessionId));
    setSessionProjectPath(projectPath);
    setTab("chat");
  }, []);

  return (
    <div className="app">
      <header className="app-header">
        <h1 className={connected ? "connected" : ""}>Chad</h1>
        <nav className="tabs">
          <button className={tab === "projects" ? "active" : ""} onClick={() => setTab("projects")}>
            Projects
          </button>
          <button className={tab === "providers" ? "active" : ""} onClick={() => setTab("providers")}>
            Providers
          </button>
          <button className={tab === "settings" ? "active" : ""} onClick={() => setTab("settings")}>
            Settings
          </button>
          {connected && sessions.some(s => openedSessionIds.has(s.id)) && (
            <>
              <span className="tab-separator" />
              {[...sessions].filter(s => openedSessionIds.has(s.id)).reverse().map((s) => (
                <button
                  key={s.id}
                  className={`session-tab ${s.id === selectedSession && tab === "chat" ? "active" : ""}`}
                  onClick={() => handleSelectSession(s.id)}
                  title={s.name}
                >
                  <span className="session-tab-name">{s.name}</span>
                  {s.active && !s.paused && <span className="badge running-badge">R</span>}
                  {s.paused && <span className="badge paused-badge">P</span>}
                  {s.has_changes && !s.active && <span className="badge changes-badge">C</span>}
                  {s.resumable && !s.active && !s.has_changes && (
                    <span className="badge" title={`${s.status} - resumable`}>
                      {s.status === "completed" ? "\u2713" : "\u25CB"}
                    </span>
                  )}
                  <span
                    className="session-tab-close"
                    onClick={(e) => handleDeleteSession(e, s.id)}
                    title="Delete session"
                  >
                    x
                  </span>
                </button>
              ))}
            </>
          )}
          {connected && (
            <button
              className="new-session-btn"
              onClick={() => handleNewSession()}
              disabled={sessionsLoading}
              title="New session"
            >
              New
            </button>
          )}
        </nav>
        {connected && apiBaseUrl && (
          <span style={{ marginLeft: "auto", fontSize: "0.8rem", opacity: 0.7 }}>
            {apiBaseUrl.replace("https://", "").replace("http://", "").replace(".trycloudflare.com", "")}
          </span>
        )}
      </header>

      <div className="app-body">
        {/* Chat view - only when Chat tab is active */}
        <div style={{ display: tab === "chat" ? "contents" : "none" }}>
          <main className="main">
            {selectedSession ? (
              <ChatView
                key={selectedSession}
                api={api}
                sessionId={selectedSession}
                onSessionChange={refreshSessions}
                defaultProjectPath={sessionProjectPath}
                apiBaseUrl={apiBaseUrl}
                token={token}
                sessionActive={selectedSessionActive}
                projects={projects}
              />
            ) : (
              <div className="placeholder">
                {projects.length === 0
                  ? "Go to the Projects tab to set up a project first."
                  : "Select or create a session to get started."}
              </div>
            )}
          </main>
        </div>
        {tab === "projects" && (
          <main className="main full-width">
            <ProjectsPanel
              api={api}
              connected={connected}
              onOpenSession={handleOpenSessionFromProject}
            />
          </main>
        )}
        {tab === "providers" && (
          <main className="main full-width">
            <ProvidersPanel api={api} connected={connected} />
          </main>
        )}
        {tab === "settings" && (
          <main className="main full-width">
            <SettingsPanel
              api={api}
              connected={connected}
              connectionInput={connectionInput}
              onConnectionInputChange={setConnectionInput}
              onConnect={(url, newToken) => {
                setApiBaseUrl(url);
                setToken(newToken);
                setSelectedSession(null);
              }}
            />
          </main>
        )}
      </div>
    </div>
  );
}
