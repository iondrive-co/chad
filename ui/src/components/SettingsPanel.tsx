import { useState, useEffect, useCallback } from "react";
import type { ChadAPI, VerificationSettings, UserPreferences } from "chad-client";

interface Props {
  api: ChadAPI;
}

export function SettingsPanel({ api }: Props) {
  const [verification, setVerification] = useState<VerificationSettings | null>(
    null,
  );
  const [preferences, setPreferences] = useState<UserPreferences | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.getVerificationSettings().then(setVerification).catch(() => {});
    api.getPreferences().then(setPreferences).catch(() => {});
  }, [api]);

  const toggleVerification = useCallback(
    async (field: keyof VerificationSettings) => {
      if (!verification) return;
      setSaving(true);
      try {
        const updated = await api.setVerificationSettings({
          [field]: !verification[field],
        });
        setVerification(updated);
      } finally {
        setSaving(false);
      }
    },
    [api, verification],
  );

  const toggleDarkMode = useCallback(async () => {
    if (!preferences) return;
    setSaving(true);
    try {
      const updated = await api.setPreferences({
        dark_mode: !preferences.dark_mode,
      });
      setPreferences(updated);
    } finally {
      setSaving(false);
    }
  }, [api, preferences]);

  return (
    <div className="settings-panel">
      <h2>Settings</h2>

      <section>
        <h3>Verification</h3>
        {verification && (
          <>
            <label className="toggle-label">
              <input
                type="checkbox"
                checked={verification.enabled}
                onChange={() => toggleVerification("enabled")}
                disabled={saving}
              />
              Verification enabled
            </label>
            <label className="toggle-label">
              <input
                type="checkbox"
                checked={verification.auto_run}
                onChange={() => toggleVerification("auto_run")}
                disabled={saving}
              />
              Auto-run verification
            </label>
          </>
        )}
      </section>

      <section>
        <h3>Preferences</h3>
        {preferences && (
          <label className="toggle-label">
            <input
              type="checkbox"
              checked={preferences.dark_mode}
              onChange={toggleDarkMode}
              disabled={saving}
            />
            Dark mode
          </label>
        )}
      </section>
    </div>
  );
}
