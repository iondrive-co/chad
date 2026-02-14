import { useState, useCallback, useEffect } from "react";
import type { ChadAPI, Account } from "chad-client";
import { AccountPicker } from "./AccountPicker.tsx";

interface Props {
  api: ChadAPI;
  sessionId: string;
  onStart: () => void;
}

export function TaskForm({ api, sessionId, onStart }: Props) {
  const [description, setDescription] = useState("");
  const [projectPath, setProjectPath] = useState("");
  const [account, setAccount] = useState<Account | null>(null);
  const [modelOverride, setModelOverride] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  const handleStart = useCallback(async () => {
    if (!description.trim() || !account || !projectPath.trim()) return;
    setStarting(true);
    setError(null);
    try {
      await api.startTask(sessionId, {
        project_path: projectPath.trim(),
        task_description: description.trim(),
        coding_agent: account.name,
        coding_model: modelOverride || undefined,
      });
      onStart();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start task");
    } finally {
      setStarting(false);
    }
  }, [api, sessionId, description, projectPath, account, modelOverride, onStart]);

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
        disabled={starting || !description.trim() || !account || !projectPath.trim()}
      >
        {starting ? "Starting..." : "Start Task"}
      </button>
    </div>
  );
}
