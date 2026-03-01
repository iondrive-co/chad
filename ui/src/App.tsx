import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { ChadAPI } from "chad-client";
import { SessionList } from "./components/SessionList.tsx";
import { ChatView } from "./components/ChatView.tsx";
import { SettingsPanel } from "./components/SettingsPanel.tsx";
import { ProvidersPanel } from "./components/ProvidersPanel.tsx";
import { useSessions } from "./hooks/useSessions.ts";

type Tab = "chat" | "providers" | "settings";

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
  const [error, setError] = useState<string | null>(null);
  const [selectedSession, setSelectedSession] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("chat");
  const [sessionVersion, setSessionVersion] = useState(0);
  const [defaultProjectPath, setDefaultProjectPath] = useState("");
  // Track whether the user has ever set a URL (vs initial empty state)
  const hasUrl = useRef(false);

  // Auto-connect when apiBaseUrl changes, retry only if we have a URL
  useEffect(() => {
    if (!apiBaseUrl) {
      // No URL set yet — stay disconnected, don't retry
      setConnected(false);
      setError(null);
      return;
    }
    hasUrl.current = true;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    setConnected(false);
    setError(null);
    const tryConnect = async () => {
      try {
        const status = await api.getStatus();
        const prefs = await api.getPreferences().catch(() => null);
        if (!cancelled) {
          setConnected(true);
          setError(null);
          if (prefs?.last_project_path) {
            setDefaultProjectPath(prefs.last_project_path);
          }
          const sessionsData = await api.listSessions();
          if (sessionsData.sessions.length > 0) {
            // Select the most recent session
            setSelectedSession(sessionsData.sessions[0].id);
          }
        }
      } catch {
        if (!cancelled) {
          setError("Waiting for Chad server...");
          timer = setTimeout(tryConnect, 1000);
        }
      }
    };
    tryConnect();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [api]);

  const { sessions, loading: sessionsLoading, createSession, deleteSession } = useSessions(
    connected ? api : null,
    sessionVersion,
  );

  // Get selected session's active state from polled data
  const selectedSessionActive = sessions.find(s => s.id === selectedSession)?.active ?? false;

  const refreshSessions = useCallback(() => {
    setSessionVersion((v) => v + 1);
  }, []);

  const connect = useCallback(() => {
    const parsed = parseConnectionInput(connectionInput);
    if (!parsed.url) return;
    setApiBaseUrl(parsed.url);
    setToken(parsed.token);
    setSelectedSession(null);
  }, [connectionInput]);

  // On initial mount, detect if we're served by the API (not file://)
  // and auto-set the base URL to the current origin
  useEffect(() => {
    if (window.location.protocol !== "file:" && !hasUrl.current && window.self === window.top) {
      setApiBaseUrl(window.location.origin);
    }
  }, []);

  return (
    <div className="app">
      <header className="app-header">
        <h1>Chad</h1>
        <span className={`status-dot${connected ? " connected" : ""}`} />
        {!connected && (
          <span className="connect-status">
            {error ?? (apiBaseUrl ? "Connecting..." : "Not connected")}
          </span>
        )}
        <nav className="tabs">
          <button className={tab === "chat" ? "active" : ""} onClick={() => setTab("chat")}>
            Chat
          </button>
          <button className={tab === "providers" ? "active" : ""} onClick={() => setTab("providers")}>
            Providers
          </button>
          <button className={tab === "settings" ? "active" : ""} onClick={() => setTab("settings")}>
            Settings
          </button>
        </nav>
        <div style={{ marginLeft: "auto", display: "flex", gap: "0.25rem", alignItems: "center" }}>
          {!connected && (
            <>
              <input
                type="text"
                placeholder="Server URL or pairing code"
                value={connectionInput}
                onChange={(e) => setConnectionInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && connect()}
                style={{ width: "14rem", padding: "0.2rem 0.4rem", fontSize: "0.85rem" }}
              />
              <button onClick={connect} style={{ fontSize: "0.85rem", padding: "0.2rem 0.6rem" }}>
                Connect
              </button>
            </>
          )}
          {connected && apiBaseUrl && (
            <span style={{ fontSize: "0.8rem", opacity: 0.7 }}>
              Remote: {apiBaseUrl.replace("https://", "").replace("http://", "").replace(".trycloudflare.com", "")}
            </span>
          )}
        </div>
      </header>

      <div className="app-body">
        {/* Keep chat content mounted so form state and merge panel survive tab switches */}
        <div style={{ display: tab === "chat" ? "contents" : "none" }}>
          <aside className="sidebar">
            <SessionList
              api={api}
              sessions={sessions}
              loading={sessionsLoading}
              createSession={createSession}
              deleteSession={deleteSession}
              selectedId={selectedSession}
              onSelect={setSelectedSession}
              onRefresh={refreshSessions}
              connected={connected}
            />
          </aside>
          <main className="main">
            {!connected ? (
              <div className="placeholder">
                Connect to a server to get started.
              </div>
            ) : selectedSession ? (
              <ChatView
                key={selectedSession}
                api={api}
                sessionId={selectedSession}
                onSessionChange={refreshSessions}
                defaultProjectPath={defaultProjectPath}
                apiBaseUrl={apiBaseUrl}
                token={token}
                sessionActive={selectedSessionActive}
              />
            ) : (
              <div className="placeholder">
                Select or create a session to get started.
              </div>
            )}
          </main>
        </div>
        {tab === "providers" && (
          <main className="main full-width">
            <ProvidersPanel api={api} connected={connected} />
          </main>
        )}
        {tab === "settings" && (
          <main className="main full-width">
            <SettingsPanel api={api} connected={connected} />
          </main>
        )}
      </div>
    </div>
  );
}
