import { useState, useCallback, useEffect } from "react";
import type { ChadAPI, Account, ProviderInfo } from "chad-client";
import { AccountPicker } from "./AccountPicker.tsx";

interface Props {
  api: ChadAPI;
  sessionId: string;
  onStart: (codingAgent: string) => void;
  defaultProjectPath?: string;
}

const REASONING_OPTIONS = ["", "low", "medium", "high"];

export function TaskForm({ api, sessionId, onStart, defaultProjectPath = "" }: Props) {
  const [description, setDescription] = useState("");
  const [projectPath, setProjectPath] = useState(defaultProjectPath);
  const [account, setAccount] = useState<Account | null>(null);
  const [modelOverride, setModelOverride] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Provider info for reasoning support
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [codingReasoning, setCodingReasoning] = useState("");

  // Verification agent fields
  const [useVerification, setUseVerification] = useState(false);
  const [verificationAccount, setVerificationAccount] = useState<Account | null>(null);
  const [verificationModel, setVerificationModel] = useState("");
  const [verificationModels, setVerificationModels] = useState<string[]>([]);
  const [verificationReasoning, setVerificationReasoning] = useState("");

  // Fetch provider info once
  useEffect(() => {
    api.listProviders()
      .then((r) => setProviders(r.providers))
      .catch(() => setProviders([]));
  }, [api]);

  // Check if current coding account's provider supports reasoning
  const codingProvider = providers.find((p) => p.type === account?.provider);
  const codingSupportsReasoning = codingProvider?.supports_reasoning ?? false;

  // Check if verification account's provider supports reasoning
  const verificationProvider = providers.find((p) => p.type === verificationAccount?.provider);
  const verificationSupportsReasoning = verificationProvider?.supports_reasoning ?? false;

  // Sync project path when default arrives from preferences (async)
  useEffect(() => {
    if (defaultProjectPath && !projectPath) {
      setProjectPath(defaultProjectPath);
    }
  }, [defaultProjectPath]); // eslint-disable-line react-hooks/exhaustive-deps

  // Fetch available models when coding account changes
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

  // Fetch available models when verification account changes
  useEffect(() => {
    if (!verificationAccount) {
      setVerificationModels([]);
      return;
    }
    api
      .getAccountModels(verificationAccount.name)
      .then((r) => setVerificationModels(r.models))
      .catch(() => setVerificationModels([]));
  }, [api, verificationAccount]);

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
        coding_reasoning: codingReasoning || undefined,
        verification_agent: useVerification && verificationAccount ? verificationAccount.name : undefined,
        verification_model: useVerification && verificationModel ? verificationModel : undefined,
        verification_reasoning: useVerification && verificationReasoning ? verificationReasoning : undefined,
      });
      onStart(account!.name);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start task");
    } finally {
      setStarting(false);
    }
  }, [
    api, sessionId, description, projectPath, account, modelOverride,
    codingReasoning, useVerification, verificationAccount, verificationModel,
    verificationReasoning, onStart, canStart,
  ]);

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

      {codingSupportsReasoning && (
        <label>
          Coding Reasoning (optional)
          <select
            value={codingReasoning}
            onChange={(e) => setCodingReasoning(e.target.value)}
          >
            {REASONING_OPTIONS.map((r) => (
              <option key={r} value={r}>
                {r || "Default"}
              </option>
            ))}
          </select>
        </label>
      )}

      {/* Verification Agent Section */}
      <div className="verification-section">
        <label className="toggle-label">
          <input
            type="checkbox"
            checked={useVerification}
            onChange={(e) => setUseVerification(e.target.checked)}
          />
          Use separate verification agent
        </label>

        {useVerification && (
          <>
            <label>
              Verification Agent
              <AccountPicker
                api={api}
                selected={verificationAccount}
                onSelect={setVerificationAccount}
              />
            </label>

            {verificationModels.length > 0 && (
              <label>
                Verification Model (optional)
                <select
                  value={verificationModel}
                  onChange={(e) => setVerificationModel(e.target.value)}
                >
                  <option value="">Default ({verificationAccount?.model ?? "auto"})</option>
                  {verificationModels.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </label>
            )}

            {verificationSupportsReasoning && (
              <label>
                Verification Reasoning (optional)
                <select
                  value={verificationReasoning}
                  onChange={(e) => setVerificationReasoning(e.target.value)}
                >
                  {REASONING_OPTIONS.map((r) => (
                    <option key={r} value={r}>
                      {r || "Default"}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </>
        )}
      </div>

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
