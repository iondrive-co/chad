import { useState, useEffect, useCallback, useRef } from "react";
import type { ChadAPI } from "chad-client";

interface Props {
  api: ChadAPI;
  projectPath: string;
  codingAgent?: string | null;
  onProjectPathChange?: (path: string) => void;
  onPromptsChange?: (codingPrompt: string | null) => void;
  onPreviewPortChange?: (port: number | null) => void;
}

interface Settings {
  project_path: string;
  project_type: string | null;
  lint_command: string | null;
  test_command: string | null;
  instructions_paths: string[];
  preview_port: number | null;
}

export function ProjectSettings({ api, projectPath, codingAgent, onProjectPathChange, onPromptsChange, onPreviewPortChange }: Props) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [editPath, setEditPath] = useState(projectPath);
  const [lintCommand, setLintCommand] = useState("");
  const [testCommand, setTestCommand] = useState("");
  const [instructionsPaths, setInstructionsPaths] = useState<string[]>([]);
  const [previewPort, setPreviewPort] = useState("");
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  // Autoconfigure state
  const [autoconfiguring, setAutoconfiguring] = useState(false);
  const [autoconfigJobId, setAutoconfigJobId] = useState<string | null>(null);
  const [autoconfigOutput, setAutoconfigOutput] = useState<string[]>([]);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const outputEndRef = useRef<HTMLDivElement | null>(null);

  // Prompts sub-panel
  const [promptsExpanded, setPromptsExpanded] = useState(false);
  const [codingPrompt, setCodingPrompt] = useState("");
  const [verificationPrompt, setVerificationPrompt] = useState("");
  const [promptsLoading, setPromptsLoading] = useState(false);

  const flash = useCallback((msg: string) => {
    setStatus(msg);
    setTimeout(() => setStatus(null), 3000);
  }, []);

  // Sync editPath when projectPath prop changes
  useEffect(() => {
    setEditPath(projectPath);
  }, [projectPath]);

  useEffect(() => {
    if (!projectPath) return;
    api.getProjectSettings(projectPath).then((s) => {
      setSettings(s);
      setLintCommand(s.lint_command || "");
      setTestCommand(s.test_command || "");
      setInstructionsPaths(s.instructions_paths || []);
      setPreviewPort(s.preview_port != null ? String(s.preview_port) : "");
      if (onPreviewPortChange) onPreviewPortChange(s.preview_port);
    }).catch(() => {
      // Ignore errors
    });
  }, [api, projectPath]);

  // Load prompts when sub-panel is expanded
  useEffect(() => {
    if (!promptsExpanded || !projectPath) return;
    setPromptsLoading(true);
    api.getPromptPreviews(projectPath).then((p) => {
      setCodingPrompt(p.coding);
      setVerificationPrompt(p.verification);
    }).catch(() => {
      // Ignore errors
    }).finally(() => {
      setPromptsLoading(false);
    });
  }, [api, projectPath, promptsExpanded]);

  // Clean up poll on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  // Auto-scroll output log
  useEffect(() => {
    outputEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [autoconfigOutput]);

  const applySettings = useCallback((s: Settings) => {
    setSettings(s);
    setLintCommand(s.lint_command || "");
    setTestCommand(s.test_command || "");
    setInstructionsPaths(s.instructions_paths || []);
    setPreviewPort(s.preview_port != null ? String(s.preview_port) : "");
    if (onPreviewPortChange) onPreviewPortChange(s.preview_port);
  }, [onPreviewPortChange]);

  const handleSave = useCallback(async () => {
    const savePath = editPath.trim() || projectPath;
    if (!savePath) return;
    setSaving(true);
    try {
      const parsedPort = previewPort.trim() ? parseInt(previewPort, 10) : null;
      const updated = await api.setProjectSettings({
        project_path: savePath,
        lint_command: lintCommand || null,
        test_command: testCommand || null,
        instructions_paths: instructionsPaths.filter(p => p.trim()),
        preview_port: (parsedPort != null && !isNaN(parsedPort)) ? parsedPort : null,
      });
      setSettings(updated);
      if (onPreviewPortChange) onPreviewPortChange(updated.preview_port);
      if (savePath !== projectPath && onProjectPathChange) {
        onProjectPathChange(savePath);
      }
      flash("Saved");
    } catch {
      flash("Error saving");
    } finally {
      setSaving(false);
    }
  }, [api, editPath, projectPath, lintCommand, testCommand, instructionsPaths, previewPort, onProjectPathChange, onPreviewPortChange, flash]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const handleAutoconfigure = useCallback(async () => {
    if (!codingAgent || !projectPath) return;
    setAutoconfiguring(true);
    setAutoconfigOutput([]);

    try {
      const { job_id } = await api.startAutoconfigure(projectPath, codingAgent);
      setAutoconfigJobId(job_id);

      pollRef.current = setInterval(async () => {
        try {
          const result = await api.getAutoconfigureResult(job_id);
          if (result.output) setAutoconfigOutput(result.output);
          if (result.status === "running") return;

          stopPolling();
          setAutoconfiguring(false);
          setAutoconfigJobId(null);

          if (result.status === "completed" && result.settings) {
            const savePath = editPath.trim() || projectPath;
            const updated = await api.setProjectSettings({
              project_path: savePath,
              lint_command: result.settings.lint_command,
              test_command: result.settings.test_command,
              instructions_paths: result.settings.instructions_paths,
              preview_port: result.settings.preview_port,
            });
            applySettings(updated);
            flash("Autoconfigured");
          } else {
            flash(result.error || "Autoconfigure failed");
          }
        } catch {
          stopPolling();
          setAutoconfiguring(false);
          setAutoconfigJobId(null);
          flash("Autoconfigure error");
        }
      }, 1500);
    } catch {
      setAutoconfiguring(false);
      flash("Failed to start autoconfigure");
    }
  }, [api, projectPath, codingAgent, editPath, applySettings, stopPolling, flash]);

  const handleCancelAutoconfigure = useCallback(async () => {
    stopPolling();
    if (autoconfigJobId) {
      try {
        await api.cancelAutoconfigure(autoconfigJobId);
      } catch {
        // ignore
      }
    }
    setAutoconfiguring(false);
    setAutoconfigJobId(null);
    flash("Cancelled");
  }, [api, autoconfigJobId, stopPolling, flash]);

  const handleProjectPathBlur = useCallback(() => {
    const trimmed = editPath.trim();
    if (trimmed && trimmed !== projectPath && onProjectPathChange) {
      onProjectPathChange(trimmed);
    }
  }, [editPath, projectPath, onProjectPathChange]);

  const addInstructionsPath = useCallback(() => {
    setInstructionsPaths(prev => [...prev, ""]);
  }, []);

  const removeInstructionsPath = useCallback((index: number) => {
    setInstructionsPaths(prev => prev.filter((_, i) => i !== index));
  }, []);

  const updateInstructionsPath = useCallback((index: number, value: string) => {
    setInstructionsPaths(prev => prev.map((p, i) => i === index ? value : p));
  }, []);

  const handleCodingPromptChange = useCallback((value: string) => {
    setCodingPrompt(value);
    if (onPromptsChange) {
      onPromptsChange(value);
    }
  }, [onPromptsChange]);

  const isUnconfigured = settings && !settings.lint_command && !settings.test_command
    && settings.instructions_paths.length === 0;

  return (
    <div className="project-settings">
      <button
        className="project-settings-toggle"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? "▼" : "▶"} Project Settings
        {settings?.project_type && settings.project_type !== "unknown" && (
          <span className="project-type">({settings.project_type})</span>
        )}
        {status && <span className="save-status">{status}</span>}
      </button>

      {expanded && (
        <div className="project-settings-content">
          {/* Autoconfigure overlay — blocks interaction while running */}
          {autoconfiguring && (
            <div className="autoconfigure-overlay">
              <div className="autoconfigure-overlay-content">
                <div className="autoconfigure-header">
                  <div className="autoconfigure-spinner" />
                  <div>Discovering project settings...</div>
                </div>
                {autoconfigOutput.length > 0 && (
                  <div className="autoconfigure-log">
                    {autoconfigOutput.map((line, i) => (
                      <div key={i}>{line || "\u00A0"}</div>
                    ))}
                    <div ref={outputEndRef} />
                  </div>
                )}
                <button className="cancel-btn" onClick={handleCancelAutoconfigure}>
                  Cancel
                </button>
              </div>
            </div>
          )}

          <div className="project-settings-row">
            <label>
              Project Path
              <input
                type="text"
                value={editPath}
                onChange={(e) => setEditPath(e.target.value)}
                onBlur={handleProjectPathBlur}
                placeholder="/home/user/project"
              />
            </label>
          </div>

          {/* Autoconfigure banner for unconfigured projects */}
          {isUnconfigured && codingAgent && !autoconfiguring && (
            <div className="autoconfigure-banner">
              Project not yet configured.
              <button
                className="autoconfigure-btn"
                onClick={handleAutoconfigure}
              >
                Autoconfigure
              </button>
            </div>
          )}

          <div className="project-settings-row">
            <label>
              Lint Command
              <input
                type="text"
                value={lintCommand}
                onChange={(e) => setLintCommand(e.target.value)}
                onBlur={handleSave}
                placeholder="e.g., flake8 ."
              />
            </label>
            <label>
              Test Command
              <input
                type="text"
                value={testCommand}
                onChange={(e) => setTestCommand(e.target.value)}
                onBlur={handleSave}
                placeholder="e.g., pytest tests/"
              />
            </label>
          </div>

          <div className="project-settings-row">
            <label>
              Preview Port
              <input
                type="number"
                value={previewPort}
                onChange={(e) => setPreviewPort(e.target.value)}
                onBlur={handleSave}
                placeholder="e.g., 3000"
                min={1}
                max={65535}
              />
            </label>
          </div>

          <div className="project-settings-section">
            <div className="project-settings-section-header">
              Agent Instructions
              <button className="add-path-btn" onClick={addInstructionsPath}>
                + Add path
              </button>
            </div>
            {instructionsPaths.map((p, i) => (
              <div key={i} className="instructions-path-row">
                <input
                  type="text"
                  value={p}
                  onChange={(e) => updateInstructionsPath(i, e.target.value)}
                  onBlur={handleSave}
                  placeholder="e.g., AGENTS.md"
                />
                <button
                  className="remove-path-btn"
                  onClick={() => { removeInstructionsPath(i); }}
                  title="Remove"
                >
                  x
                </button>
              </div>
            ))}
            {instructionsPaths.length === 0 && (
              <div className="instructions-hint">
                No instruction files configured. Click &quot;+ Add path&quot; to add one.
              </div>
            )}
          </div>

          {/* Prompts sub-panel */}
          <div className="project-settings-section">
            <button
              className="project-settings-toggle prompts-toggle"
              onClick={() => setPromptsExpanded(!promptsExpanded)}
            >
              {promptsExpanded ? "▼" : "▶"} Prompts
            </button>
            {promptsExpanded && (
              <div className="prompts-panel">
                {promptsLoading ? (
                  <div className="loading">Loading prompts...</div>
                ) : (
                  <>
                    <div className="prompt-note">
                      Use <code>{"{task}"}</code> as a placeholder for the task description.
                    </div>
                    <label>
                      Coding Prompt
                      <textarea
                        className="prompt-textarea"
                        value={codingPrompt}
                        onChange={(e) => handleCodingPromptChange(e.target.value)}
                        rows={12}
                      />
                    </label>
                    <label>
                      Verification Prompt
                      <textarea
                        className="prompt-textarea"
                        value={verificationPrompt}
                        onChange={(e) => setVerificationPrompt(e.target.value)}
                        rows={8}
                        readOnly
                      />
                    </label>
                  </>
                )}
              </div>
            )}
          </div>

          <div className="project-settings-actions">
            <button onClick={handleSave} disabled={saving}>
              {saving ? "Saving..." : "Save"}
            </button>
            {codingAgent && (
              <button
                className="autoconfigure-btn"
                onClick={handleAutoconfigure}
                disabled={autoconfiguring || !projectPath}
                title="Use AI agent to discover project settings"
              >
                Autoconfigure
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
