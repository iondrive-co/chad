import { useState, useEffect, useCallback } from "react";
import type { ChadAPI, Account, ProviderInfo } from "chad-client";

interface Props {
  api: ChadAPI;
}

export function ProvidersPanel({ api }: Props) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState("anthropic");
  const [newApiKey, setNewApiKey] = useState("");
  const [adding, setAdding] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [editingModel, setEditingModel] = useState<string | null>(null);
  const [modelChoices, setModelChoices] = useState<string[]>([]);

  const flash = useCallback((msg: string) => {
    setStatus(msg);
    setTimeout(() => setStatus(null), 3000);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [a, p] = await Promise.all([api.listAccounts(), api.listProviders()]);
      setAccounts(a.accounts);
      setProviders(p.providers);
    } catch { /* */ }
  }, [api]);

  useEffect(() => { refresh(); }, [refresh]);

  const needsApiKey = newType === "opencode" || newType === "mistral";

  const handleAdd = useCallback(async () => {
    if (!newName.trim()) return;
    setAdding(true);
    setStatus(null);
    try {
      await api.createAccount({ name: newName.trim(), provider: newType as Account["provider"] });
      setNewName("");
      setNewApiKey("");
      flash(`Added ${newName.trim()}`);
      await refresh();
    } catch (e) {
      flash(e instanceof Error ? e.message : "Failed to add provider");
    } finally {
      setAdding(false);
    }
  }, [api, newName, newType, refresh, flash]);

  const handleDelete = useCallback(async (name: string) => {
    try {
      await api.deleteAccount(name);
      flash(`Deleted ${name}`);
      await refresh();
    } catch (e) {
      flash(e instanceof Error ? e.message : "Delete failed");
    }
  }, [api, refresh, flash]);

  const handleSetRole = useCallback(async (name: string, role: string) => {
    try {
      await api.setAccountRole(name, role);
      flash("Role updated");
      await refresh();
    } catch { /* */ }
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

  return (
    <div className="providers-panel">
      <div className="section-header">
        <h2>Providers</h2>
        {status && <span className="save-status">{status}</span>}
      </div>

      {/* Account list */}
      <div className="account-list">
        {accounts.map((a) => (
          <div key={a.name} className={`account-card ${a.ready ? "" : "not-ready"}`}>
            <div className="account-header">
              <span className="account-name">{a.name}</span>
              <span className="account-provider">{a.provider}</span>
              <span className={`account-status ${a.ready ? "ready" : ""}`}>
                {a.ready ? "Ready" : "Not ready"}
              </span>
              <button className="delete-rule-btn" onClick={() => handleDelete(a.name)}>x</button>
            </div>

            <div className="account-details">
              <div className="account-field">
                <span className="field-label">Model:</span>
                <button className="link-btn" onClick={() => handleModelClick(a.name)}>
                  {a.model ?? "default"}
                </button>
                {editingModel === a.name && (
                  <select
                    value={a.model ?? ""}
                    onChange={(e) => handleSetModel(a.name, e.target.value)}
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
                  onChange={(e) => handleSetRole(a.name, e.target.value)}
                >
                  <option value="">None</option>
                  <option value="CODING">Coding</option>
                  <option value="VERIFICATION">Verification</option>
                </select>
              </div>

              {providers.find((p) => p.type === a.provider)?.supports_reasoning && (
                <div className="account-field">
                  <span className="field-label">Reasoning:</span>
                  <select
                    value={a.reasoning ?? ""}
                    onChange={(e) => handleSetReasoning(a.name, e.target.value)}
                  >
                    <option value="">Default</option>
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                  </select>
                </div>
              )}
            </div>
          </div>
        ))}
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
          />
          <select value={newType} onChange={(e) => setNewType(e.target.value)}>
            {providers.map((p) => (
              <option key={p.type} value={p.type}>{p.name}</option>
            ))}
          </select>
          {needsApiKey && (
            <input
              type="password"
              value={newApiKey}
              onChange={(e) => setNewApiKey(e.target.value)}
              placeholder="API Key"
            />
          )}
          <button onClick={handleAdd} disabled={adding || !newName.trim()}>
            {adding ? "Adding..." : "+ Add"}
          </button>
        </div>
      </div>
    </div>
  );
}
