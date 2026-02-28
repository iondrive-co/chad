import { useState, useCallback, useEffect, useMemo } from "react";
import { ChadAPI } from "chad-client";
import { SessionList } from "./components/SessionList.tsx";
import { ChatView } from "./components/ChatView.tsx";
import { SettingsPanel } from "./components/SettingsPanel.tsx";
import { ProvidersPanel } from "./components/ProvidersPanel.tsx";

type Tab = "chat" | "providers" | "settings";

export function App() {
  const [apiBaseUrl, setApiBaseUrl] = useState("");
  const [pairingCode, setPairingCode] = useState("");
  const [token, setToken] = useState<string | undefined>(undefined);
  const api = useMemo(() => new ChadAPI(apiBaseUrl, token), [apiBaseUrl, token]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedSession, setSelectedSession] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("chat");
  const [sessionVersion, setSessionVersion] = useState(0);
  const [defaultProjectPath, setDefaultProjectPath] = useState("");

  // Auto-connect on mount, retry until server is up
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    setConnected(false);
    setError(null);
    const tryConnect = async () => {
      try {
        const status = await api.getStatus();
        if (!cancelled) {
          setConnected(true);
          setError(null);
          // Use server cwd as default project path
          if (status.cwd) {
            setDefaultProjectPath(status.cwd);
          }
          // Auto-create "Task 1" session on first connect if none selected
          const sessionsData = await api.listSessions();
          if (sessionsData.sessions.length === 0) {
            // No sessions - create first one
            const newSession = await api.createSession({
              name: "Task 1",
              project_path: status.cwd || null,
            });
            setSelectedSession(newSession.id);
            setSessionVersion((v) => v + 1);
          } else if (!sessionsData.sessions.some((s) => s.active)) {
            // No active sessions - select the most recent one
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

  const refreshSessions = useCallback(() => {
    setSessionVersion((v) => v + 1);
  }, []);

  const connectToTunnel = useCallback(() => {
    const code = pairingCode.trim();
    if (!code) return;
    // Pairing code format: "subdomain:token" or just "subdomain" (no auth)
    const colonIdx = code.indexOf(":");
    if (colonIdx > 0) {
      const subdomain = code.slice(0, colonIdx);
      const pairToken = code.slice(colonIdx + 1);
      setApiBaseUrl(`https://${subdomain}.trycloudflare.com`);
      setToken(pairToken);
    } else {
      setApiBaseUrl(`https://${code}.trycloudflare.com`);
      setToken(undefined);
    }
    setSelectedSession(null);
  }, [pairingCode]);

  if (!connected) {
    return (
      <div className="app">
        <header className="app-header">
          <h1>Chad</h1>
          <span className="status-dot" />
          <span className="connect-status">
            {error ?? "Connecting..."}
          </span>
          <div style={{ marginLeft: "auto", display: "flex", gap: "0.25rem", alignItems: "center" }}>
            <input
              type="text"
              placeholder="Pairing code"
              value={pairingCode}
              onChange={(e) => setPairingCode(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && connectToTunnel()}
              style={{ width: "12rem", padding: "0.2rem 0.4rem", fontSize: "0.85rem" }}
            />
            <button onClick={connectToTunnel} style={{ fontSize: "0.85rem", padding: "0.2rem 0.6rem" }}>
              Connect
            </button>
          </div>
        </header>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>Chad</h1>
        <span className="status-dot connected" />
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
        {apiBaseUrl && (
          <span style={{ marginLeft: "auto", fontSize: "0.8rem", opacity: 0.7 }}>
            Remote: {apiBaseUrl.replace("https://", "").replace(".trycloudflare.com", "")}
          </span>
        )}
      </header>

      <div className="app-body">
        {/* Keep chat content mounted so form state and merge panel survive tab switches */}
        <div style={{ display: tab === "chat" ? "contents" : "none" }}>
          <aside className="sidebar">
            <SessionList
              api={api}
              selectedId={selectedSession}
              onSelect={setSelectedSession}
              version={sessionVersion}
              onRefresh={refreshSessions}
            />
          </aside>
          <main className="main">
            {selectedSession ? (
              <ChatView
                key={selectedSession}
                api={api}
                sessionId={selectedSession}
                onSessionChange={refreshSessions}
                defaultProjectPath={defaultProjectPath}
                apiBaseUrl={apiBaseUrl}
                token={token}
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
            <ProvidersPanel api={api} />
          </main>
        )}
        {tab === "settings" && (
          <main className="main full-width">
            <SettingsPanel api={api} />
          </main>
        )}
      </div>
    </div>
  );
}
