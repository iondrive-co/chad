import { useState, useEffect, useCallback } from "react";
import type { ChadAPI } from "chad-client";

interface Props {
  api: ChadAPI;
  projectPath: string;
  onProjectPathChange?: (path: string) => void;
  onPromptsChange?: (codingPrompt: string | null) => void;
}

interface Settings {
  project_path: string;
  project_type: string | null;
  lint_command: string | null;
  test_command: string | null;
  instructions_paths: string[];
}

export function ProjectSettings({ api, projectPath, onProjectPathChange, onPromptsChange }: Props) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [editPath, setEditPath] = useState(projectPath);
  const [lintCommand, setLintCommand] = useState("");
  const [testCommand, setTestCommand] = useState("");
  const [instructionsPaths, setInstructionsPaths] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  // Prompts sub-panel
  const [promptsExpanded, setPromptsExpanded] = useState(false);
  const [codingPrompt, setCodingPrompt] = useState("");
  const [verificationPrompt, setVerificationPrompt] = useState("");
  const [promptsLoading, setPromptsLoading] = useState(false);

  const flash = useCallback((msg: string) => {
    setStatus(msg);
    setTimeout(() => setStatus(null), 2000);
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

  const handleSave = useCallback(async () => {
    const savePath = editPath.trim() || projectPath;
    if (!savePath) return;
    setSaving(true);
    try {
      const updated = await api.setProjectSettings({
        project_path: savePath,
        lint_command: lintCommand || null,
        test_command: testCommand || null,
        instructions_paths: instructionsPaths.filter(p => p.trim()),
      });
      setSettings(updated);
      if (savePath !== projectPath && onProjectPathChange) {
        onProjectPathChange(savePath);
      }
      flash("Saved");
    } catch {
      flash("Error saving");
    } finally {
      setSaving(false);
    }
  }, [api, editPath, projectPath, lintCommand, testCommand, instructionsPaths, onProjectPathChange, flash]);

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

  // Notify parent when coding prompt changes
  const handleCodingPromptChange = useCallback((value: string) => {
    setCodingPrompt(value);
    if (onPromptsChange) {
      onPromptsChange(value);
    }
  }, [onPromptsChange]);

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
          </div>
        </div>
      )}
    </div>
  );
}
