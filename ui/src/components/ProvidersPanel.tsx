import { useState, useEffect, useCallback, useRef } from "react";
import type { ChadAPI, Account, ProviderInfo, AccountUsage } from "chad-client";

interface Props {
  api: ChadAPI;
  connected: boolean;
}

// Account names become directory names on the server, which restricts them to
// this charset. Checked here so a bad name gets an explanation instead of the
// bare "HTTP 422" a schema rejection surfaces.
const ACCOUNT_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const ACCOUNT_NAME_HINT = "Names start with a letter or digit and use only letters, digits, . _ -";
// The tray has room for three characters per account, so that is all a code is.
const ACCOUNT_CODE_RE = /^[A-Za-z0-9]{1,3}$/;
const ACCOUNT_CODE_HINT = "A code is 1 to 3 letters or digits";

export function ProvidersPanel({ api, connected }: Props) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [usageData, setUsageData] = useState<Record<string, AccountUsage>>({});
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState("anthropic");
  const [localEndpoint, setLocalEndpoint] = useState("http://localhost:8000");
  const [adding, setAdding] = useState(false);
  const [loggingIn, setLoggingIn] = useState<string | null>(null);
  const [loginKeys, setLoginKeys] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<string | null>(null);
  const [editingModel, setEditingModel] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [editingCode, setEditingCode] = useState<string | null>(null);
  const [codeValue, setCodeValue] = useState("");
  const [modelChoices, setModelChoices] = useState<string[]>([]);
  const [refreshingUsage, setRefreshingUsage] = useState<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const isApiKeyProvider = (provider: string) => provider === "mistral";

  const flash = useCallback((msg: string) => {
    setStatus(msg);
    setTimeout(() => setStatus(null), 3000);
  }, []);

  const refreshUsage = useCallback(async (accountNames: string[]) => {
    const usagePromises = accountNames.map(async (name) => {
      try {
        const usage = await api.getAccountUsage(name);
        return { name, usage };
      } catch {
        return null;
      }
    });
    const results = await Promise.all(usagePromises);
    const newUsageData: Record<string, AccountUsage> = {};
    for (const result of results) {
      if (result) {
        newUsageData[result.name] = result.usage;
      }
    }
    setUsageData(prev => ({...prev, ...newUsageData}));
  }, [api]);

  // Re-read usage for one account (picks up snapshots written since load — e.g.
  // after a run in the standalone CLI on the same account).
  const refreshUsageLive = useCallback(async (name: string) => {
    setRefreshingUsage(name);
    try {
      const usage = await api.getAccountUsage(name);
      setUsageData(prev => ({ ...prev, [name]: usage }));
    } catch {
      flash("Could not refresh usage");
    } finally {
      setRefreshingUsage(null);
    }
  }, [api, flash]);

  const refresh = useCallback(async () => {
    try {
      const [a, p] = await Promise.all([api.listAccounts(), api.listProviders()]);
      setAccounts(a.accounts);
      setProviders(p.providers);
      // Refresh usage for all accounts
      await refreshUsage(a.accounts.map((acc) => acc.name));
    } catch { /* */ }
  }, [api, refreshUsage]);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    api.getLocalEndpoint().then((r) => setLocalEndpoint(r.endpoint)).catch(() => {});
  }, [api]);

  // Keep the Add-provider type in sync with the loaded provider list so the
  // submitted type always matches what the select shows.
  useEffect(() => {
    if (providers.length === 0) return;
    setNewType((prev) => (providers.some((p) => p.type === prev) ? prev : providers[0].type));
  }, [providers]);

  const pollReady = useCallback(async (name: string) => {
    // Install + browser OAuth complete out-of-band; poll until ready.
    for (let i = 0; i < 180; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      if (!mountedRef.current) return;
      try {
        const acc = await api.getAccount(name);
        if (!mountedRef.current) return;
        if (acc.ready) {
          flash(`${name} logged in`);
          await refresh();
          return;
        }
      } catch { /* keep polling */ }
    }
    if (!mountedRef.current) return;
    flash("Login not completed — try again");
    await refresh();
  }, [api, refresh, flash]);

  const handleLogin = useCallback(async (name: string) => {
    setLoggingIn(name);
    setStatus(null);
    try {
      const res = await api.loginAccount(name, loginKeys[name] ?? "");
      if (res.ready) {
        flash(`${name} logged in`);
        await refresh();
      } else if (res.success) {
        flash(res.message);
        await pollReady(name);
      } else {
        flash(res.message);
      }
    } catch (e) {
      flash(e instanceof Error ? e.message : "Login failed");
    } finally {
      setLoggingIn(null);
    }
  }, [api, loginKeys, refresh, flash, pollReady]);

  const handleAdd = useCallback(async () => {
    if (!newName.trim()) return;
    const name = newName.trim();
    if (!ACCOUNT_NAME_RE.test(name)) {
      flash(ACCOUNT_NAME_HINT);
      return;
    }
    const provider = newType;
    setAdding(true);
    setStatus(null);
    try {
      if (provider === "local") {
        const r = await api.setLocalEndpoint(localEndpoint);
        setLocalEndpoint(r.endpoint);
      }
      await api.createAccount({ name, provider: provider as Account["provider"] });
      setNewName("");
      flash(`Added ${name}`);
      await refresh();
    } catch (e) {
      flash(e instanceof Error ? e.message : "Failed to add provider");
      setAdding(false);
      return;
    }
    setAdding(false);
    // Browser-OAuth providers can log in straight away; API-key providers need
    // the user to paste a key first, so leave them with the Log in button.
    if (!isApiKeyProvider(provider)) {
      await handleLogin(name);
    }
  }, [api, newName, newType, localEndpoint, refresh, flash, handleLogin]);

  const handleRename = useCallback(async (name: string) => {
    const target = renameValue.trim();
    if (!target || target === name) {
      setRenaming(null);
      return;
    }
    if (!ACCOUNT_NAME_RE.test(target)) {
      flash(ACCOUNT_NAME_HINT);
      return;
    }
    try {
      await api.renameAccount(name, target);
      setRenaming(null);
      flash(`Renamed to ${target}`);
      await refresh();
    } catch (e) {
      // The input stays open so the name can be corrected in place.
      flash(e instanceof Error ? e.message : "Rename failed");
    }
  }, [api, renameValue, refresh, flash]);

  const handleSetCode = useCallback(async (name: string) => {
    const target = codeValue.trim().toUpperCase();
    if (!target) {
      setEditingCode(null);
      return;
    }
    if (!ACCOUNT_CODE_RE.test(target)) {
      flash(ACCOUNT_CODE_HINT);
      return;
    }
    try {
      await api.setAccountCode(name, target);
      setEditingCode(null);
      flash(`Tray code set to ${target}`);
      await refresh();
    } catch (e) {
      flash(e instanceof Error ? e.message : "Failed to set the code");
    }
  }, [api, codeValue, refresh, flash]);

  const handleDelete = useCallback(async (name: string) => {
    try {
      await api.deleteAccount(name);
      flash(`Deleted ${name}`);
      await refresh();
    } catch (e) {
      flash(e instanceof Error ? e.message : "Delete failed");
    }
  }, [api, refresh, flash]);

  const handleSetRole = useCallback(async (name: string, role: "CODING" | null) => {
    try {
      await api.setAccountRole(name, role);
      flash("Role updated");
      await refresh();
    } catch (e) {
      flash(e instanceof Error ? e.message : "Failed to update role");
    }
  }, [api, refresh, flash]);

  const handleSetReasoning = useCallback(async (name: string, reasoning: string) => {
    try {
      await api.setAccountReasoning(name, reasoning);
      flash("Reasoning updated");
      await refresh();
    } catch { /* */ }
  }, [api, refresh, flash]);

  const handleModelClick = useCallback(async (name: string) => {
    if (editingModel === name) {
      setEditingModel(null);
      return;
    }
    setEditingModel(name);
    try {
      const r = await api.getAccountModels(name);
      setModelChoices(r.models);
    } catch {
      setModelChoices([]);
    }
  }, [api, editingModel]);

  const handleSetModel = useCallback(async (name: string, model: string) => {
    try {
      await api.setAccountModel(name, model);
      setEditingModel(null);
      flash("Model updated");
      await refresh();
    } catch { /* */ }
  }, [api, refresh, flash]);

  // A window the provider answered about and did not meter: OpenAI dropped the
  // Codex 5-hour limit for Business/Team plans on 2026-07-12, so a team account
  // reports a weekly pool and nothing else. Leaving the row out made a plan's
  // own limits look like a bug in Chad.
  const NO_LIMIT = "No limit on this account";

  const formatUsage = (pct: number | null, eta: string | null): string => {
    if (pct === null) return NO_LIMIT;
    const bar = Math.round(pct / 10);
    const filled = "█".repeat(bar);
    const empty = "░".repeat(10 - bar);
    let text = `${filled}${empty} ${pct.toFixed(0)}%`;
    if (eta) text += ` (resets ${eta})`;
    return text;
  };

  // Relative age of a usage snapshot, e.g. "3 days ago". Returns null if unknown.
  const formatAsOf = (asOf: string | null | undefined): string | null => {
    if (!asOf) return null;
    const ms = Date.now() - new Date(asOf).getTime();
    if (!Number.isFinite(ms) || ms < 0) return "just now";
    const mins = Math.floor(ms / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  };

  const dis = !connected;

  return (
    <div className="providers-panel">
      <div className="section-header">
        <h2>Providers</h2>
        {status && <span className="save-status">{status}</span>}
      </div>

      {dis && (
        <p style={{ color: "#999", fontStyle: "italic" }}>Connect to a server to manage providers.</p>
      )}

      {/* Account list */}
      <div className="account-list">
        {accounts.map((a) => {
          const usage = usageData[a.name];
          return (
            <div key={a.name} className={`account-card ${a.ready ? "" : "not-ready"}`}>
              <div className="account-header">
                {renaming === a.name ? (
                  <span className="account-rename">
                    <input
                      type="text"
                      value={renameValue}
                      autoFocus
                      onChange={(e) => setRenameValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleRename(a.name);
                        if (e.key === "Escape") setRenaming(null);
                      }}
                      disabled={dis}
                    />
                    <button onClick={() => handleRename(a.name)} disabled={dis}>Save</button>
                    <button className="link-btn" onClick={() => setRenaming(null)}>Cancel</button>
                  </span>
                ) : (
                  <button
                    className="account-name account-name-btn"
                    title="Rename this account"
                    onClick={() => { setRenaming(a.name); setRenameValue(a.name); }}
                    disabled={dis}
                  >
                    {a.name}
                  </button>
                )}
                <span className="account-provider">{a.provider}</span>
                {editingCode === a.name ? (
                  <input
                    className="account-code-input"
                    type="text"
                    value={codeValue}
                    autoFocus
                    maxLength={3}
                    onChange={(e) => setCodeValue(e.target.value.toUpperCase())}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleSetCode(a.name);
                      if (e.key === "Escape") setEditingCode(null);
                    }}
                    onBlur={() => handleSetCode(a.name)}
                    disabled={dis}
                  />
                ) : (
                  <button
                    className="account-code"
                    title="The code this account shows under in the tray — click to change"
                    onClick={() => { setEditingCode(a.name); setCodeValue(a.code); }}
                    disabled={dis}
                  >
                    {a.code || "—"}
                  </button>
                )}
                <span className={`account-status ${a.ready ? "ready" : ""}`}>
                  {a.ready ? "Ready" : "Logged out"}
                </span>
                <button className="delete-rule-btn" onClick={() => handleDelete(a.name)} disabled={dis}>x</button>
              </div>

              {!a.ready && (
                <div className="account-login">
                  <span className="login-hint">
                    Log in to authorize this account. Tasks can't run until you do.
                  </span>
                  {isApiKeyProvider(a.provider) && (
                    <input
                      type="password"
                      placeholder="API Key"
                      value={loginKeys[a.name] ?? ""}
                      onChange={(e) =>
                        setLoginKeys((prev) => ({ ...prev, [a.name]: e.target.value }))
                      }
                      disabled={dis || loggingIn === a.name}
                    />
                  )}
                  <button
                    className="login-btn"
                    onClick={() => handleLogin(a.name)}
                    disabled={dis || loggingIn === a.name}
                  >
                    {loggingIn === a.name ? "Logging in…" : "Log in"}
                  </button>
                </div>
              )}

              <div className="account-details">
                <div className="account-field">
                  <span className="field-label">Model:</span>
                  <button className="link-btn" onClick={() => handleModelClick(a.name)} disabled={dis}>
                    {a.model ?? "default"}
                  </button>
                  {editingModel === a.name && (
                    <select
                      value={a.model ?? ""}
                      onChange={(e) => handleSetModel(a.name, e.target.value)}
                      disabled={dis}
                    >
                      {modelChoices.map((m) => (
                        <option key={m} value={m}>{m}</option>
                      ))}
                    </select>
                  )}
                </div>

                <div className="account-field">
                  <span className="field-label">Role:</span>
                  <select
                    value={a.role ?? ""}
                    onChange={(e) => handleSetRole(a.name, e.target.value === "CODING" ? "CODING" : null)}
                    disabled={dis}
                  >
                    <option value="">None</option>
                    <option value="CODING">Coding</option>
                  </select>
                </div>

                {(providers.find((p) => p.type === a.provider)?.reasoning_levels ?? []).length > 0 && (
                  <div className="account-field">
                    <span className="field-label">Reasoning:</span>
                    <select
                      value={a.reasoning ?? ""}
                      onChange={(e) => handleSetReasoning(a.name, e.target.value)}
                      disabled={dis}
                    >
                      <option value="">Default</option>
                      {(providers.find((p) => p.type === a.provider)?.reasoning_levels ?? []).map((level) => (
                        <option key={level} value={level}>
                          {level.charAt(0).toUpperCase() + level.slice(1)}
                        </option>
                      ))}
                    </select>
                  </div>
                )}

                {/* A logged-out account has no usage to report — say so, rather
                    than leaving the row blank or showing a fabricated 0%. */}
                {usage?.logged_out && (
                  <div className="account-usage">
                    <div className="usage-row">
                      <span className="field-label">Usage:</span>
                      <span className="usage-bar">Logged out — log in to see usage</span>
                    </div>
                  </div>
                )}

                {/* Usage display. Both windows are listed whenever the account
                    reports either, so a window with no limit on it says so
                    rather than going quietly missing. */}
                {usage && !usage.logged_out && (usage.session_usage_pct !== null || usage.weekly_usage_pct !== null) && (
                  <div className="account-usage">
                    <div className="usage-row">
                      <span className="field-label">Session:</span>
                      <span className={usage.session_usage_pct === null ? "usage-absent" : "usage-bar"}>
                        {formatUsage(usage.session_usage_pct, usage.session_reset_eta)}
                      </span>
                    </div>
                    <div className="usage-row">
                      <span className="field-label">Weekly:</span>
                      <span className={usage.weekly_usage_pct === null ? "usage-absent" : "usage-bar"}>
                        {formatUsage(usage.weekly_usage_pct, usage.weekly_reset_eta)}
                      </span>
                    </div>
                    <div className="usage-row">
                      {formatAsOf(usage.usage_as_of) && (
                        <span className="usage-as-of">as of {formatAsOf(usage.usage_as_of)}</span>
                      )}
                      <button
                        className="link-btn"
                        onClick={() => refreshUsageLive(a.name)}
                        disabled={dis || refreshingUsage === a.name}
                      >
                        {refreshingUsage === a.name ? "Refreshing…" : "Refresh"}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Add provider form */}
      <div className="add-provider-form">
        <h3>Add Provider</h3>
        <div className="add-provider-row">
          <input
            type="text"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Account name"
            disabled={dis}
          />
          <select value={newType} onChange={(e) => setNewType(e.target.value)} disabled={dis}>
            {providers.map((p) => (
              <option key={p.type} value={p.type}>{p.name}</option>
            ))}
          </select>
          {newType === "local" && (
            <input
              type="text"
              value={localEndpoint}
              onChange={(e) => setLocalEndpoint(e.target.value)}
              placeholder="http://localhost:8000"
              title="Host and port of the local OpenAI-compatible model server"
              disabled={dis}
            />
          )}
          <button onClick={handleAdd} disabled={adding || !newName.trim() || dis}>
            {adding ? "Adding..." : "+ Add"}
          </button>
        </div>
      </div>
    </div>
  );
}
