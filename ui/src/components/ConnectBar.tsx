import { useState, useCallback } from "react";
import { ChadAPI } from "chad-client";

interface Props {
  onConnect: (url: string, api: ChadAPI) => void;
  onDisconnect: () => void;
  connected: boolean;
}

export function ConnectBar({ onConnect, onDisconnect, connected }: Props) {
  const [url, setUrl] = useState("http://localhost:8000");
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleConnect = useCallback(async () => {
    setConnecting(true);
    setError(null);
    try {
      const api = new ChadAPI(url);
      await api.getStatus();
      onConnect(url, api);
    } catch {
      setError("Cannot reach server");
    } finally {
      setConnecting(false);
    }
  }, [url, onConnect]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !connected) handleConnect();
    },
    [connected, handleConnect],
  );

  return (
    <div className="connect-bar">
      <span className={`status-dot ${connected ? "connected" : ""}`} />
      <input
        type="text"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="http://localhost:8000"
        disabled={connected || connecting}
      />
      {connected ? (
        <button onClick={onDisconnect}>Disconnect</button>
      ) : (
        <button onClick={handleConnect} disabled={connecting}>
          {connecting ? "Connecting..." : "Connect"}
        </button>
      )}
      {error && <span className="error-text">{error}</span>}
    </div>
  );
}
