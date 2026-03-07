import { useState, useEffect, useCallback } from "react";
import type { ChadAPI, VerificationSettings, Account } from "chad-client";
import { ActionRules } from "./ActionRules.tsx";
import { QRScanner } from "./QRScanner.tsx";
import { parseConnectionInput } from "../App.tsx";

interface Props {
  api: ChadAPI;
  connected: boolean;
  connectionInput?: string;
  onConnectionInputChange?: (value: string) => void;
  onConnect?: (url: string, token?: string) => void;
}

export function SettingsPanel({
  api,
  connected,
  connectionInput = "",
  onConnectionInputChange,
  onConnect,
}: Props) {
  const [verification, setVerification] = useState<VerificationSettings | null>(null);
  const [maxAttempts, setMaxAttempts] = useState<number>(3);
  const [verificationAgent, setVerificationAgent] = useState<string | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [retentionDays, setRetentionDays] = useState<number>(7);
  const [slackEnabled, setSlackEnabled] = useState(false);
  const [slackChannel, setSlackChannel] = useState("");
  const [slackHasToken, setSlackHasToken] = useState(false);
  const [tunnelRunning, setTunnelRunning] = useState(false);
  const [tunnelUrl, setTunnelUrl] = useState<string | null>(null);
  const [tunnelSubdomain, setTunnelSubdomain] = useState<string | null>(null);
  const [tunnelError, setTunnelError] = useState<string | null>(null);
  const [tunnelLoading, setTunnelLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [localConnectionInput, setLocalConnectionInput] = useState(connectionInput);

  const flash = useCallback((msg: string) => {
    setStatus(msg);
    setTimeout(() => setStatus(null), 2000);
  }, []);

  useEffect(() => {
    api.getVerificationSettings().then(setVerification).catch(() => {});
    api.getMaxVerificationAttempts().then((r) => setMaxAttempts(r.attempts)).catch(() => {});
    api.getVerificationAgent().then((r) => setVerificationAgent(r.account_name)).catch(() => {});
    api.listAccounts().then((r) => setAccounts(r.accounts)).catch(() => {});
    api.getCleanupSettings().then((r) => setRetentionDays(r.cleanup_days)).catch(() => {});
    api.getSlackSettings().then((r) => {
      setSlackEnabled(r.enabled);
      setSlackChannel(r.channel ?? "");
      setSlackHasToken(r.has_token);
    }).catch(() => {});
    api.getTunnelStatus().then((r) => {
      setTunnelRunning(r.running);
      setTunnelUrl(r.url);
      setTunnelSubdomain(r.subdomain);
      setTunnelError(r.error);
    }).catch(() => {});
  }, [api]);

  // ── Verification ──

  const toggleVerification = useCallback(async (field: keyof VerificationSettings) => {
    if (!verification) return;
    setSaving(true);
    try {
      const updated = await api.setVerificationSettings({
        [field]: !verification[field],
      });
      setVerification(updated);
      flash("Saved");
    } catch { /* ignore */ } finally {
      setSaving(false);
    }
  }, [api, verification, flash]);

  const saveMaxAttempts = useCallback(async (val: number) => {
    setMaxAttempts(val);
    try {
      await api.setMaxVerificationAttempts(val);
      flash("Saved");
    } catch { /* */ }
  }, [api, flash]);

  const saveVerificationAgent = useCallback(async (name: string) => {
    const val = name || null;
    setVerificationAgent(val);
    try {
      await api.setVerificationAgent(val);
      flash("Saved");
    } catch { /* */ }
  }, [api, flash]);

  // ── Cleanup ──

  const saveRetention = useCallback(async (days: number) => {
    if (days < 1) return;
    setRetentionDays(days);
    try {
      await api.setCleanupSettings({ cleanup_days: days });
      flash("Saved");
    } catch { /* */ }
  }, [api, flash]);

  // ── Slack ──

  const saveSlack = useCallback(async (update: Record<string, unknown>) => {
    try {
      const r = await api.setSlackSettings(update as Record<string, string | boolean>);
      setSlackEnabled(r.enabled);
      setSlackChannel(r.channel ?? "");
      setSlackHasToken(r.has_token);
      flash("Saved");
    } catch { /* */ }
  }, [api, flash]);

  // ── Tunnel ──

  const toggleTunnel = useCallback(async () => {
    setTunnelLoading(true);
    setTunnelError(null);
    try {
      const r = tunnelRunning
        ? await api.stopTunnel()
        : await api.startTunnel();
      setTunnelRunning(r.running);
      setTunnelUrl(r.url);
      setTunnelSubdomain(r.subdomain);
      setTunnelError(r.error);
    } catch {
      setTunnelError("Request failed");
    } finally {
      setTunnelLoading(false);
    }
  }, [api, tunnelRunning]);

  // ── Connection ──

  const handleConnect = useCallback(() => {
    const parsed = parseConnectionInput(localConnectionInput);
    if (parsed.url && onConnect) {
      onConnect(parsed.url, parsed.token);
    }
  }, [localConnectionInput, onConnect]);

  const handleScan = useCallback((code: string) => {
    setScanning(false);
    const parsed = parseConnectionInput(code);
    if (parsed.url && onConnect) {
      setLocalConnectionInput(code);
      onConnectionInputChange?.(code);
      onConnect(parsed.url, parsed.token);
    }
  }, [onConnect, onConnectionInputChange]);

  const dis = !connected;

  return (
    <div className="settings-panel">
      <div className="section-header">
        <h2>Settings</h2>
        {status && <span className="save-status">{status}</span>}
      </div>

      {/* ── Connection (shown when not connected) ── */}
      {dis && (
        <section className="connection-section">
          <div className="placeholder-card">
            {scanning ? (
              <QRScanner onScan={handleScan} onCancel={() => setScanning(false)} />
            ) : (
              <>
                <h3>Connect to a Chad server</h3>
                <div className="placeholder-steps">
                  <p>
                    1) Get the latest Chad server from{" "}
                    <a
                      href="https://github.com/iondrive-co/chad/releases"
                      target="_blank"
                      rel="noreferrer"
                    >
                      the releases page
                    </a>
                    .
                  </p>
                  <p>
                    2) Run it on an isolated machine with <code>chad --tunnel</code>.
                  </p>
                  <p>
                    3) Scan the QR code it displays, or paste the pairing key below.
                  </p>
                </div>
                <div style={{ display: "flex", gap: "0.5rem", marginTop: "1rem", flexWrap: "wrap" }}>
                  <input
                    type="text"
                    placeholder="Server URL or pairing code"
                    value={localConnectionInput}
                    onChange={(e) => {
                      setLocalConnectionInput(e.target.value);
                      onConnectionInputChange?.(e.target.value);
                    }}
                    onKeyDown={(e) => e.key === "Enter" && handleConnect()}
                    style={{ flex: "1", minWidth: "12rem", padding: "0.4rem 0.6rem" }}
                  />
                  <button onClick={handleConnect} style={{ padding: "0.4rem 1rem" }}>
                    Connect
                  </button>
                </div>
                <button
                  className="scan-qr-btn"
                  onClick={() => setScanning(true)}
                  style={{ marginTop: "1rem" }}
                >
                  Scan QR Code
                </button>
              </>
            )}
          </div>
        </section>
      )}

      {/* ── Verification ── */}
      <section>
        <h3>Verification</h3>
        {verification && (
          <>
            <label className="toggle-label">
              <input type="checkbox" checked={verification.enabled}
                onChange={() => toggleVerification("enabled")} disabled={saving || dis} />
              Verification enabled
            </label>
          </>
        )}
        <label>
          Max verification attempts
          <input type="number" min={1} max={20} value={maxAttempts}
            onChange={(e) => saveMaxAttempts(Number(e.target.value))} disabled={dis} />
        </label>
        <label>
          Verification agent
          <select value={verificationAgent ?? ""}
            onChange={(e) => saveVerificationAgent(e.target.value)} disabled={dis}>
            <option value="">Same as coding agent</option>
            {accounts.map((a) => (
              <option key={a.name} value={a.name}>{a.name} ({a.provider})</option>
            ))}
          </select>
        </label>
      </section>

      {/* ── Action Rules ── */}
      <ActionRules api={api} connected={connected} />

      {/* ── Cleanup ── */}
      <section>
        <h3>Cleanup</h3>
        <label>
          Retention days
          <input type="number" min={1} value={retentionDays}
            onChange={(e) => saveRetention(Number(e.target.value))} disabled={dis} />
        </label>
      </section>

      {/* ── Remote Access (Tunnel) ── */}
      <section>
        <h3>Remote Access</h3>
        <div style={{ marginBottom: "0.5rem" }}>
          <strong>Status:</strong>{" "}
          {tunnelRunning ? (
            <span style={{ color: "#4caf50" }}>Running</span>
          ) : (
            <span style={{ color: "#999" }}>Stopped</span>
          )}
        </div>
        {tunnelUrl && (
          <div style={{ marginBottom: "0.5rem" }}>
            <strong>URL:</strong>{" "}
            <a href={tunnelUrl} target="_blank" rel="noopener noreferrer">{tunnelUrl}</a>
          </div>
        )}
        {tunnelSubdomain && (
          <div style={{ marginBottom: "0.5rem" }}>
            <strong>Pairing code:</strong>{" "}
            <code>{tunnelSubdomain}</code>
          </div>
        )}
        {tunnelError && (
          <div style={{ marginBottom: "0.5rem", color: "#f44336" }}>
            {tunnelError}
          </div>
        )}
        <button onClick={toggleTunnel} disabled={tunnelLoading || dis}>
          {tunnelLoading ? "..." : tunnelRunning ? "Stop Tunnel" : "Start Tunnel"}
        </button>
      </section>

      {/* ── Config Export / Import ── */}
      <section>
        <h3>Config Transfer</h3>
        <p style={{ fontSize: "0.85rem", color: "#999", marginBottom: "0.5rem" }}>
          Export your config (with encrypted keys) to set up a headless server.
        </p>
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
          <button
            disabled={dis}
            onClick={async () => {
              try {
                const data = await api.exportConfig();
                const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = "chad-config.json";
                a.click();
                URL.revokeObjectURL(url);
                flash("Config exported");
              } catch {
                flash("Export failed");
              }
            }}
          >
            Export Config
          </button>
          <button
            onClick={() => {
              const input = document.createElement("input");
              input.type = "file";
              input.accept = ".json";
              input.onchange = async () => {
                const file = input.files?.[0];
                if (!file) return;
                try {
                  const text = await file.text();
                  const data = JSON.parse(text);
                  await api.importConfig(data);
                  flash("Config imported — reload to apply");
                } catch {
                  flash("Import failed — check file format");
                }
              };
              input.click();
            }}
          >
            Import Config
          </button>
        </div>
      </section>

      {/* ── Slack ── */}
      <section>
        <h3>Slack Integration</h3>
        <label className="toggle-label">
          <input type="checkbox" checked={slackEnabled}
            onChange={() => saveSlack({ enabled: !slackEnabled })} disabled={dis} />
          Enabled
        </label>
        <label>
          Channel ID
          <input type="text" value={slackChannel} placeholder="C0123456789"
            onBlur={(e) => saveSlack({ channel: e.target.value })}
            onChange={(e) => setSlackChannel(e.target.value)} disabled={dis} />
        </label>
        <label>
          Bot Token
          <input type="password" placeholder={slackHasToken ? "•••••••••" : "xoxb-..."}
            disabled={dis}
            onBlur={(e) => {
              if (e.target.value && !e.target.value.startsWith("•"))
                saveSlack({ bot_token: e.target.value });
            }} />
        </label>
      </section>
    </div>
  );
}
