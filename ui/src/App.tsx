import { useState, useCallback } from "react";
import { ChadAPI } from "chad-client";
import { ConnectBar } from "./components/ConnectBar.tsx";
import { SessionList } from "./components/SessionList.tsx";
import { ChatView } from "./components/ChatView.tsx";
import { SettingsPanel } from "./components/SettingsPanel.tsx";

type Tab = "chat" | "settings";

export function App() {
  const [api, setApi] = useState<ChadAPI | null>(null);
  const [serverUrl, setServerUrl] = useState("");
  const [selectedSession, setSelectedSession] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("chat");
  const [sessionVersion, setSessionVersion] = useState(0);

  const handleConnect = useCallback((url: string, client: ChadAPI) => {
    setServerUrl(url);
    setApi(client);
    setSelectedSession(null);
  }, []);

  const handleDisconnect = useCallback(() => {
    setApi(null);
    setServerUrl("");
    setSelectedSession(null);
  }, []);

  const refreshSessions = useCallback(() => {
    setSessionVersion((v) => v + 1);
  }, []);

  return (
    <div className="app">
      <header className="app-header">
        <h1>Chad</h1>
        <ConnectBar
          onConnect={handleConnect}
          onDisconnect={handleDisconnect}
          connected={api != null}
        />
        {api && (
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
        )}
      </header>

      {api && (
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
                    serverUrl={serverUrl}
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
      )}
    </div>
  );
}
