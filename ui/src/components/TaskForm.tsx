import { useState, useCallback, useEffect, useRef, DragEvent } from "react";
import type { ChadAPI, Account, ProviderInfo, VerificationSettings } from "chad-client";
import { AccountPicker } from "./AccountPicker.tsx";

interface Props {
  api: ChadAPI;
  sessionId: string;
  onStart: (codingAgent: string) => void;
  defaultProjectPath?: string;
}

interface UploadedScreenshot {
  path: string;
  filename: string;
  previewUrl: string;
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

  // Screenshot attachments
  const [screenshots, setScreenshots] = useState<UploadedScreenshot[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Provider info for reasoning support
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [codingReasoning, setCodingReasoning] = useState("");

  // Verification agent fields
  const [useVerification, setUseVerification] = useState(false);
  const [verificationAccount, setVerificationAccount] = useState<Account | null>(null);
  const [verificationModel, setVerificationModel] = useState("");
  const [verificationModels, setVerificationModels] = useState<string[]>([]);
  const [verificationReasoning, setVerificationReasoning] = useState("");
  const [verificationSettings, setVerificationSettings] = useState<VerificationSettings | null>(null);
  const verificationDefaultsApplied = useRef(false);

  // Fetch provider info once
  useEffect(() => {
    api.listProviders()
      .then((r) => setProviders(r.providers))
      .catch(() => setProviders([]));
  }, [api]);

  // Load verification settings + default verification agent
  useEffect(() => {
    let cancelled = false;

    api.getVerificationSettings()
      .then((settings) => {
        if (cancelled) return;
        setVerificationSettings(settings);
        // On first load, align toggle with auto_run; if disabled, force off.
        if (!settings.enabled) {
          setUseVerification(false);
          setVerificationAccount(null);
        } else if (!verificationDefaultsApplied.current) {
          setUseVerification(settings.auto_run);
          verificationDefaultsApplied.current = true;
        }
      })
      .catch(() => {
        if (!cancelled) {
          setVerificationSettings({ enabled: true, auto_run: true });
        }
      });

    api.getVerificationAgent()
      .then((r) => {
        if (cancelled) return;
        const name = r.account_name;
        if (!name || name === "__verification_none__") return;
        api.getAccount(name)
          .then((acct) => {
            if (!cancelled) setVerificationAccount(acct);
          })
          .catch(() => { /* ignore missing account */ });
      })
      .catch(() => {});

    return () => { cancelled = true; };
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

  // Screenshot upload handlers
  const handleFiles = useCallback(async (files: FileList | File[]) => {
    const imageFiles = Array.from(files).filter((f) =>
      f.type.startsWith("image/")
    );
    if (imageFiles.length === 0) return;

    setUploading(true);
    setError(null);

    for (const file of imageFiles) {
      try {
        const result = await api.uploadFile(file);
        const previewUrl = URL.createObjectURL(file);
        setScreenshots((prev) => [
          ...prev,
          { path: result.path, filename: result.filename, previewUrl },
        ]);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to upload screenshot");
      }
    }
    setUploading(false);
  }, [api]);

  const handleDrop = useCallback(
    (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setDragOver(false);
      if (e.dataTransfer.files.length > 0) {
        handleFiles(e.dataTransfer.files);
      }
    },
    [handleFiles]
  );

  const handleDragOver = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
  }, []);

  const removeScreenshot = useCallback((index: number) => {
    setScreenshots((prev) => {
      const removed = prev[index];
      if (removed?.previewUrl) {
        URL.revokeObjectURL(removed.previewUrl);
      }
      return prev.filter((_, i) => i !== index);
    });
  }, []);

  const missingFields: string[] = [];
  if (!projectPath.trim()) missingFields.push("project path");
  if (!description.trim()) missingFields.push("task description");
  if (!account) missingFields.push("coding agent");
  const canStart = missingFields.length === 0 && !starting && !uploading;
  const disabledReason = starting
    ? "Starting..."
    : uploading
      ? "Uploading..."
      : missingFields.length > 0
        ? `Missing: ${missingFields.join(", ")}`
        : "";

  const handleStart = useCallback(async () => {
    if (!canStart) return;
    setStarting(true);
    setError(null);
    try {
      const verificationAllowed = verificationSettings?.enabled && useVerification;
      await api.startTask(sessionId, {
        project_path: projectPath.trim(),
        task_description: description.trim(),
        coding_agent: account!.name,
        coding_model: modelOverride || undefined,
        coding_reasoning: codingReasoning || undefined,
        verification_agent: verificationAllowed && verificationAccount ? verificationAccount.name : undefined,
        verification_model: verificationAllowed && verificationModel ? verificationModel : undefined,
        verification_reasoning: verificationAllowed && verificationReasoning ? verificationReasoning : undefined,
        screenshots: screenshots.length > 0 ? screenshots.map((s) => s.path) : undefined,
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
    verificationReasoning, verificationSettings, onStart, canStart, screenshots,
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

      {/* Screenshot Drop Zone */}
      <div className="screenshot-section">
        <div className="screenshot-label">Screenshots (optional)</div>
        <div
          className={`screenshot-dropzone ${dragOver ? "drag-over" : ""}`}
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onClick={() => fileInputRef.current?.click()}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            multiple
            style={{ display: "none" }}
            onChange={(e) => e.target.files && handleFiles(e.target.files)}
          />
          {uploading ? (
            <span>Uploading...</span>
          ) : (
            <span>Drop images here or click to browse</span>
          )}
        </div>
        {screenshots.length > 0 && (
          <div className="screenshot-previews">
            {screenshots.map((s, i) => (
              <div key={s.path} className="screenshot-preview">
                <img src={s.previewUrl} alt={s.filename} />
                <button
                  type="button"
                  className="screenshot-remove"
                  onClick={() => removeScreenshot(i)}
                  title="Remove"
                >
                  x
                </button>
                <span className="screenshot-name">{s.filename}</span>
              </div>
            ))}
          </div>
        )}
      </div>

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
            disabled={verificationSettings?.enabled === false}
          />
          Use separate verification agent
        </label>

        {verificationSettings?.enabled === false && (
          <div className="field-label">Verification disabled in settings</div>
        )}

        {useVerification && verificationSettings?.enabled !== false && (
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
