import { useState, useEffect, useCallback } from "react";
import type { ChadAPI, VerificationSettings, Account, AutostartSettings } from "chad-client";
import { ActionRules } from "./ActionRules.tsx";
import { QRScanner } from "./QRScanner.tsx";
import { parseConnectionInput } from "../App.tsx";

interface Props {
  api: ChadAPI;
  connected: boolean;
  apiBaseUrl: string;
  connectionInput?: string;
  onConnectionInputChange?: (value: string) => void;
  onConnect?: (url: string, token?: string) => void;
}

export function SettingsPanel({
  api,
  connected,
  apiBaseUrl,
  connectionInput = "",
  onConnectionInputChange,
  onConnect,
}: Props) {
  const [verification, setVerification] = useState<VerificationSettings | null>(null);
  const [maxAttempts, setMaxAttempts] = useState<number>(3);
  const [maxAttemptsInput, setMaxAttemptsInput] = useState<string>("3");
  const [verificationAgent, setVerificationAgent] = useState<string | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [retentionDays, setRetentionDays] = useState<number>(7);
  const [retentionInput, setRetentionInput] = useState<string>("7");
  const [localEndpoint, setLocalEndpoint] = useState("");
  const [autostart, setAutostart] = useState<AutostartSettings | null>(null);
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
  const [importing, setImporting] = useState(false);

  const flash = useCallback((msg: string) => {
    setStatus(msg);
    setTimeout(() => setStatus(null), 2000);
  }, []);

  // Keep the connection field in sync when the parent-owned value changes
  // (e.g. after a QR scan or a connect initiated elsewhere).
  useEffect(() => {
    setLocalConnectionInput(connectionInput);
  }, [connectionInput]);

  useEffect(() => {
    if (!connected) return;
    api.getVerificationSettings().then(setVerification).catch(() => {});
    api.getMaxVerificationAttempts().then((r) => {
      setMaxAttempts(r.attempts);
      setMaxAttemptsInput(String(r.attempts));
    }).catch(() => {});
    api.getVerificationAgent().then((r) => setVerificationAgent(r.account_name)).catch(() => {});
    api.listAccounts().then((r) => setAccounts(r.accounts)).catch(() => {});
    api.getCleanupSettings().then((r) => {
      setRetentionDays(r.cleanup_days);
      setRetentionInput(String(r.cleanup_days));
    }).catch(() => {});
    api.getLocalEndpoint().then((r) => setLocalEndpoint(r.endpoint)).catch(() => {});
    api.getAutostart().then(setAutostart).catch(() => {});
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
  }, [api, connected]);

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

  // Commit-on-blur: keep local text state while typing, validate, and only
  // persist a valid value. Invalid input restores the last known good value.
  const commitMaxAttempts = useCallback(async () => {
    const val = Number(maxAttemptsInput);
    if (!Number.isInteger(val) || val < 1) {
      setMaxAttemptsInput(String(maxAttempts));
      return;
    }
    if (val === maxAttempts) return;
    try {
      const r = await api.setMaxVerificationAttempts(val);
      setMaxAttempts(r.attempts);
      setMaxAttemptsInput(String(r.attempts));
      flash("Saved");
    } catch {
      setMaxAttemptsInput(String(maxAttempts));
      flash("Failed to save max verification attempts");
    }
  }, [api, maxAttempts, maxAttemptsInput, flash]);

  const saveVerificationAgent = useCallback(async (name: string) => {
    const val = name || null;
    setVerificationAgent(val);
    try {
      await api.setVerificationAgent(val);
      flash("Saved");
    } catch { /* */ }
  }, [api, flash]);

  // ── Start at login ──

  const toggleAutostart = useCallback(async () => {
    if (!autostart) return;
    setSaving(true);
    try {
      setAutostart(await api.setAutostart(!autostart.enabled));
      flash("Saved");
    } catch {
      flash("Failed to change start at login");
    } finally {
      setSaving(false);
    }
  }, [api, autostart, flash]);

  // ── Cleanup ──

  const commitRetention = useCallback(async () => {
    const days = Number(retentionInput);
    if (!Number.isInteger(days) || days < 1) {
      setRetentionInput(String(retentionDays));
      return;
    }
    if (days === retentionDays) return;
    try {
      const r = await api.setCleanupSettings({ cleanup_days: days });
      setRetentionDays(r.cleanup_days);
      setRetentionInput(String(r.cleanup_days));
      flash("Saved");
    } catch {
      setRetentionInput(String(retentionDays));
      flash("Failed to save retention days");
    }
  }, [api, retentionDays, retentionInput, flash]);

  // ── Local model ──

  const saveLocalEndpoint = useCallback(async (endpoint: string) => {
    try {
      const r = await api.setLocalEndpoint(endpoint);
      setLocalEndpoint(r.endpoint);
      flash("Saved");
    } catch {
      flash("Invalid endpoint — must be an http(s) URL");
    }
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
      // Starting a tunnel publishes this server, so the server requires auth
      // from now on. Adopt the token it returned or this tab's next request
      // 401s.
      if (r.token && onConnect) {
        onConnect(apiBaseUrl, r.token);
      }
    } catch {
      setTunnelError("Request failed");
    } finally {
      setTunnelLoading(false);
    }
  }, [api, tunnelRunning, onConnect, apiBaseUrl]);

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
              Verify tasks by default
            </label>
            <p className="instructions-hint">
              Seeds the Verification Agent picker for new sessions. Choosing an
              agent there verifies that session either way.
            </p>
          </>
        )}
        <label>
          Max verification attempts
          <input type="number" min={1} max={20} value={maxAttemptsInput}
            onChange={(e) => setMaxAttemptsInput(e.target.value)}
            onBlur={() => commitMaxAttempts()}
            onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
            disabled={dis} />
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

      {/* ── Startup ── */}
      <section>
        <h3>Startup</h3>
        <label className="toggle-label">
          <input type="checkbox" checked={autostart?.enabled ?? false}
            onChange={() => toggleAutostart()}
            disabled={dis || !autostart?.supported} />
          Start Chad at login, in the system tray
        </label>
        <p className="instructions-hint">
          {autostart?.supported
            ? autostart.enabled
              ? `Chad starts with your desktop and waits in the tray — ${autostart.location}`
              : "Chad starts with your desktop and waits in the tray; click it to open this window."
            : "The machine running this server has no system tray."}
        </p>
      </section>

      {/* ── Cleanup ── */}
      <section>
        <h3>Cleanup</h3>
        <label>
          Retention days
          <input type="number" min={1} value={retentionInput}
            onChange={(e) => setRetentionInput(e.target.value)}
            onBlur={() => commitRetention()}
            onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
            disabled={dis} />
        </label>
      </section>

      {/* ── Local Model ── */}
      <section>
        <h3>Local Model</h3>
        <label>
          Server endpoint
          <input type="text" value={localEndpoint} placeholder="http://localhost:8000"
            onChange={(e) => setLocalEndpoint(e.target.value)}
            onBlur={(e) => saveLocalEndpoint(e.target.value)} disabled={dis} />
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
          Export your settings to set up another machine. Provider logins are only
          included if you give a passphrase, and are encrypted with it.
        </p>
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
          <button
            disabled={dis}
            onClick={async () => {
              const passphrase = window.prompt(
                "Passphrase to encrypt provider logins in the export.\n" +
                "Leave blank to export settings only (no credentials).",
                "",
              );
              if (passphrase === null) return;
              try {
                const data = await api.exportConfig(passphrase || null);
                const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = "chad-config.json";
                a.click();
                URL.revokeObjectURL(url);
                flash(
                  data.credentials_included
                    ? "Config exported with encrypted logins"
                    : "Config exported (settings only)",
                );
              } catch {
                flash("Export failed");
              }
            }}
          >
            Export Config
          </button>
          <button
            disabled={dis || importing}
            onClick={() => {
              const input = document.createElement("input");
              input.type = "file";
              input.accept = ".json";
              input.onchange = async () => {
                const file = input.files?.[0];
                if (!file) return;
                let passphrase: string | null = null;
                let data: Record<string, unknown>;
                try {
                  data = JSON.parse(await file.text());
                } catch {
                  flash("Import failed — check file format");
                  return;
                }
                if (data.provider_auth_encrypted) {
                  passphrase = window.prompt(
                    "This export contains encrypted provider logins.\nEnter the passphrase used to create it:",
                    "",
                  );
                  if (passphrase === null) return;
                }
                setImporting(true);
                try {
                  const result = await api.importConfig(data, passphrase);
                  if (result.install_errors && Object.keys(result.install_errors).length > 0) {
                    const failed = Object.entries(result.install_errors)
                      .map(([tool, err]) => `${tool}: ${err.split("\n")[0]}`)
                      .join("; ");
                    flash(`Config imported but some tools failed: ${failed}`);
                  } else {
                    flash("Config imported successfully");
                  }
                } catch (err) {
                  // Surface the server's reason (e.g. wrong passphrase)
                  const body = (err as { body?: { detail?: string } })?.body;
                  flash(body?.detail ? `Import failed — ${body.detail}` : "Import failed");
                } finally {
                  setImporting(false);
                }
              };
              input.click();
            }}
          >
            {importing ? "Importing…" : "Import Config"}
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
