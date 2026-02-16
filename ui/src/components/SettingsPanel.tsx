import { useState, useEffect, useCallback } from "react";
import type { ChadAPI, VerificationSettings, UserPreferences, Account } from "chad-client";
import { ActionRules } from "./ActionRules.tsx";

interface Props {
  api: ChadAPI;
}

export function SettingsPanel({ api }: Props) {
  const [verification, setVerification] = useState<VerificationSettings | null>(null);
  const [preferences, setPreferences] = useState<UserPreferences | null>(null);
  const [maxAttempts, setMaxAttempts] = useState<number>(3);
  const [verificationAgent, setVerificationAgent] = useState<string | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [retentionDays, setRetentionDays] = useState<number>(7);
  const [slackEnabled, setSlackEnabled] = useState(false);
  const [slackChannel, setSlackChannel] = useState("");
  const [slackHasToken, setSlackHasToken] = useState(false);
  const [slackHasSecret, setSlackHasSecret] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const flash = useCallback((msg: string) => {
    setStatus(msg);
    setTimeout(() => setStatus(null), 2000);
  }, []);

  useEffect(() => {
    api.getVerificationSettings().then(setVerification).catch(() => {});
    api.getPreferences().then(setPreferences).catch(() => {});
    api.getMaxVerificationAttempts().then((r) => setMaxAttempts(r.attempts)).catch(() => {});
    api.getVerificationAgent().then((r) => setVerificationAgent(r.account_name)).catch(() => {});
    api.listAccounts().then((r) => setAccounts(r.accounts)).catch(() => {});
    api.getCleanupSettings().then((r) => setRetentionDays(r.cleanup_days)).catch(() => {});
    api.getSlackSettings().then((r) => {
      setSlackEnabled(r.enabled);
      setSlackChannel(r.channel ?? "");
      setSlackHasToken(r.has_token);
      setSlackHasSecret(r.has_signing_secret);
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

  // ── Preferences ──

  const toggleDarkMode = useCallback(async () => {
    if (!preferences) return;
    setSaving(true);
    try {
      const updated = await api.setPreferences({ dark_mode: !preferences.dark_mode });
      setPreferences(updated);
      flash("Saved");
    } finally {
      setSaving(false);
    }
  }, [api, preferences, flash]);

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
      setSlackHasSecret(r.has_signing_secret);
      flash("Saved");
    } catch { /* */ }
  }, [api, flash]);

  return (
    <div className="settings-panel">
      <div className="section-header">
        <h2>Settings</h2>
        {status && <span className="save-status">{status}</span>}
      </div>

      {/* ── Verification ── */}
      <section>
        <h3>Verification</h3>
        {verification && (
          <>
            <label className="toggle-label">
              <input type="checkbox" checked={verification.enabled}
                onChange={() => toggleVerification("enabled")} disabled={saving} />
              Verification enabled
            </label>
            <label className="toggle-label">
              <input type="checkbox" checked={verification.auto_run}
                onChange={() => toggleVerification("auto_run")} disabled={saving} />
              Auto-run verification
            </label>
          </>
        )}
        <label>
          Max verification attempts
          <input type="number" min={1} max={20} value={maxAttempts}
            onChange={(e) => saveMaxAttempts(Number(e.target.value))} />
        </label>
        <label>
          Verification agent
          <select value={verificationAgent ?? ""}
            onChange={(e) => saveVerificationAgent(e.target.value)}>
            <option value="">Same as coding agent</option>
            {accounts.map((a) => (
              <option key={a.name} value={a.name}>{a.name} ({a.provider})</option>
            ))}
          </select>
        </label>
      </section>

      {/* ── Action Rules ── */}
      <ActionRules api={api} />

      {/* ── Preferences ── */}
      <section>
        <h3>Preferences</h3>
        {preferences && (
          <label className="toggle-label">
            <input type="checkbox" checked={preferences.dark_mode}
              onChange={toggleDarkMode} disabled={saving} />
            Dark mode
          </label>
        )}
      </section>

      {/* ── Cleanup ── */}
      <section>
        <h3>Cleanup</h3>
        <label>
          Retention days
          <input type="number" min={1} value={retentionDays}
            onChange={(e) => saveRetention(Number(e.target.value))} />
        </label>
      </section>

      {/* ── Slack ── */}
      <section>
        <h3>Slack Integration</h3>
        <label className="toggle-label">
          <input type="checkbox" checked={slackEnabled}
            onChange={() => saveSlack({ enabled: !slackEnabled })} />
          Enabled
        </label>
        <label>
          Channel ID
          <input type="text" value={slackChannel} placeholder="C0123456789"
            onBlur={(e) => saveSlack({ channel: e.target.value })}
            onChange={(e) => setSlackChannel(e.target.value)} />
        </label>
        <label>
          Bot Token
          <input type="password" placeholder={slackHasToken ? "•••••••••" : "xoxb-..."}
            onBlur={(e) => {
              if (e.target.value && !e.target.value.startsWith("•"))
                saveSlack({ bot_token: e.target.value });
            }} />
        </label>
        <label>
          Signing Secret
          <input type="password" placeholder={slackHasSecret ? "•••••••••" : "Enter signing secret"}
            onBlur={(e) => {
              if (e.target.value && !e.target.value.startsWith("•"))
                saveSlack({ signing_secret: e.target.value });
            }} />
        </label>
      </section>
    </div>
  );
}
