import { useState, useCallback, useEffect, useMemo } from "react";
import { ChadAPI } from "chad-client";
import { SessionList } from "./components/SessionList.tsx";
import { ChatView } from "./components/ChatView.tsx";
import { SettingsPanel } from "./components/SettingsPanel.tsx";

type Tab = "chat" | "settings";

export function App() {
  // Use same origin — Vite proxy forwards /api and /ws to Chad server
  const api = useMemo(() => new ChadAPI(""), []);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedSession, setSelectedSession] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("chat");
  const [sessionVersion, setSessionVersion] = useState(0);

  // Auto-connect on mount, retry until server is up
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const tryConnect = async () => {
      try {
        await api.getStatus();
        if (!cancelled) {
          setConnected(true);
          setError(null);
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

  if (!connected) {
    return (
      <div className="app">
        <header className="app-header">
          <h1>Chad</h1>
          <span className="status-dot" />
          <span className="connect-status">
            {error ?? "Connecting..."}
          </span>
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
          <button
            className={tab === "chat" ? "active" : ""}
            onClick={() => setTab("chat")}
          >
            Chat
          </button>
          <button
            className={tab === "settings" ? "active" : ""}
            onClick={() => setTab("settings")}
          >
            Settings
          </button>
        </nav>
      </header>

      <div className="app-body">
        {tab === "chat" ? (
          <>
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
                  api={api}
                  serverUrl=""
                  sessionId={selectedSession}
                  onSessionChange={refreshSessions}
                />
              ) : (
                <div className="placeholder">
                  Select or create a session to get started.
                </div>
              )}
            </main>
          </>
        ) : (
          <main className="main full-width">
            <SettingsPanel api={api} />
          </main>
        )}
      </div>
    </div>
  );
}
