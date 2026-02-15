import { useState, useEffect, useCallback } from "react";
import type { ChadAPI, Account } from "chad-client";

interface ActionRule {
  event: string;
  threshold: number;
  action: string;
  target_account: string | null;
}

const EVENT_TYPES = [
  { value: "session_usage", label: "Session Usage" },
  { value: "weekly_usage", label: "Weekly Usage" },
  { value: "context_usage", label: "Context Usage" },
];

const ACTIONS = [
  { value: "notify", label: "Notify" },
  { value: "switch_provider", label: "Switch Provider" },
  { value: "await_reset", label: "Await Reset" },
];

const MAX_RULES = 6;

function emptyRule(): ActionRule {
  return { event: "session_usage", threshold: 80, action: "notify", target_account: null };
}

interface Props {
  api: ChadAPI;
}

export function ActionRules({ api }: Props) {
  const [rules, setRules] = useState<ActionRule[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    api.getActionSettings()
      .then((r) => setRules((r.settings ?? []) as ActionRule[]))
      .catch(() => {});
    api.listAccounts()
      .then((r) => setAccounts(r.accounts))
      .catch(() => {});
  }, [api]);

  const save = useCallback(async (updated: ActionRule[]) => {
    setRules(updated);
    try {
      await api.setActionSettings(updated);
      setStatus("Saved");
      setTimeout(() => setStatus(null), 1500);
    } catch {
      setStatus("Save failed");
    }
  }, [api]);

  const updateRule = useCallback((index: number, field: keyof ActionRule, value: string | number | null) => {
    const updated = rules.map((r, i) => {
      if (i !== index) return r;
      const next = { ...r, [field]: value };
      if (field === "action" && value !== "switch_provider") {
        next.target_account = null;
      }
      return next;
    });
    save(updated);
  }, [rules, save]);

  const addRule = useCallback(() => {
    if (rules.length >= MAX_RULES) return;
    save([...rules, emptyRule()]);
  }, [rules, save]);

  const deleteRule = useCallback((index: number) => {
    save(rules.filter((_, i) => i !== index));
  }, [rules, save]);

  return (
    <section className="action-rules">
      <div className="section-header">
        <h3>Action Rules</h3>
        {status && <span className="save-status">{status}</span>}
      </div>

      {rules.map((rule, i) => (
        <div key={i} className="action-rule-row">
          <select
            value={rule.event}
            onChange={(e) => updateRule(i, "event", e.target.value)}
          >
            {EVENT_TYPES.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>

          <div className="threshold-group">
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={rule.threshold}
              onChange={(e) => updateRule(i, "threshold", Number(e.target.value))}
            />
            <span className="threshold-value">{rule.threshold}%</span>
          </div>

          <select
            value={rule.action}
            onChange={(e) => updateRule(i, "action", e.target.value)}
          >
            {ACTIONS.map((a) => (
              <option key={a.value} value={a.value}>{a.label}</option>
            ))}
          </select>

          {rule.action === "switch_provider" && (
            <select
              value={rule.target_account ?? ""}
              onChange={(e) => updateRule(i, "target_account", e.target.value || null)}
            >
              <option value="">Select account...</option>
              {accounts.map((a) => (
                <option key={a.name} value={a.name}>{a.name}</option>
              ))}
            </select>
          )}

          <button className="delete-rule-btn" onClick={() => deleteRule(i)}>x</button>
        </div>
      ))}

      {rules.length < MAX_RULES && (
        <button className="add-rule-btn" onClick={addRule}>+ Add Rule</button>
      )}
    </section>
  );
}
