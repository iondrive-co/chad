import { useState, useCallback, useEffect } from "react";
import type { ChadAPI, Account } from "chad-client";
import { AccountPicker } from "./AccountPicker.tsx";

interface Props {
  api: ChadAPI;
  sessionId: string;
  onStart: () => void;
  defaultProjectPath?: string;
}

export function TaskForm({ api, sessionId, onStart, defaultProjectPath = "" }: Props) {
  const [description, setDescription] = useState("");
  const [projectPath, setProjectPath] = useState(defaultProjectPath);
  const [account, setAccount] = useState<Account | null>(null);
  const [modelOverride, setModelOverride] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Sync project path when default arrives from preferences (async)
  useEffect(() => {
    if (defaultProjectPath && !projectPath) {
      setProjectPath(defaultProjectPath);
    }
  }, [defaultProjectPath]); // eslint-disable-line react-hooks/exhaustive-deps

  // Fetch available models when account changes
  useEffect(() => {
    if (!account) {
      setModels([]);
      return;
    }
    api
      .getAccountModels(account.name)
      .then((r) => setModels(r.models))
      .catch(() => setModels([]));
  }, [api, account]);

  const missingFields: string[] = [];
  if (!projectPath.trim()) missingFields.push("project path");
  if (!description.trim()) missingFields.push("task description");
  if (!account) missingFields.push("coding agent");
  const canStart = missingFields.length === 0 && !starting;
  const disabledReason = starting
    ? "Starting..."
    : missingFields.length > 0
      ? `Missing: ${missingFields.join(", ")}`
      : "";

  const handleStart = useCallback(async () => {
    if (!canStart) return;
    setStarting(true);
    setError(null);
    try {
      await api.startTask(sessionId, {
        project_path: projectPath.trim(),
        task_description: description.trim(),
        coding_agent: account!.name,
        coding_model: modelOverride || undefined,
      });
      onStart();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start task");
    } finally {
      setStarting(false);
    }
  }, [api, sessionId, description, projectPath, account, modelOverride, onStart, canStart]);

  return (
    <div className="task-form">
      <h3>Start a Task</h3>

      <label>
        Project Path
        <input
          type="text"
          value={projectPath}
          onChange={(e) => setProjectPath(e.target.value)}
          placeholder="/home/user/project"
        />
      </label>

      <label>
        Task Description
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Describe what you want the AI to do..."
          rows={4}
        />
      </label>

      <label>
        Coding Agent
        <AccountPicker api={api} selected={account} onSelect={setAccount} />
      </label>

      {models.length > 0 && (
        <label>
          Model Override (optional)
          <select
            value={modelOverride}
            onChange={(e) => setModelOverride(e.target.value)}
          >
            <option value="">Default ({account?.model ?? "auto"})</option>
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
      )}

      {error && <div className="error-text">{error}</div>}

      <button
        className="start-btn"
        onClick={handleStart}
        disabled={!canStart}
        title={disabledReason}
      >
        {starting ? "Starting..." : "Start Task"}
      </button>
    </div>
  );
}
