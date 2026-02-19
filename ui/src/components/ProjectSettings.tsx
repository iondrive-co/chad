import { useState, useEffect, useCallback } from "react";
import type { ChadAPI } from "chad-client";

interface Props {
  api: ChadAPI;
  projectPath: string;
}

interface Settings {
  project_path: string;
  project_type: string | null;
  lint_command: string | null;
  test_command: string | null;
  instructions_path: string | null;
  architecture_path: string | null;
}

export function ProjectSettings({ api, projectPath }: Props) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [lintCommand, setLintCommand] = useState("");
  const [testCommand, setTestCommand] = useState("");
  const [instructionsPath, setInstructionsPath] = useState("");
  const [architecturePath, setArchitecturePath] = useState("");
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const flash = useCallback((msg: string) => {
    setStatus(msg);
    setTimeout(() => setStatus(null), 2000);
  }, []);

  useEffect(() => {
    if (!projectPath) return;
    api.getProjectSettings(projectPath).then((s) => {
      setSettings(s);
      setLintCommand(s.lint_command || "");
      setTestCommand(s.test_command || "");
      setInstructionsPath(s.instructions_path || "");
      setArchitecturePath(s.architecture_path || "");
    }).catch(() => {
      // Ignore errors
    });
  }, [api, projectPath]);

  const handleSave = useCallback(async () => {
    if (!projectPath) return;
    setSaving(true);
    try {
      const updated = await api.setProjectSettings({
        project_path: projectPath,
        lint_command: lintCommand || null,
        test_command: testCommand || null,
        instructions_path: instructionsPath || null,
        architecture_path: architecturePath || null,
      });
      setSettings(updated);
      flash("Saved");
    } catch {
      flash("Error saving");
    } finally {
      setSaving(false);
    }
  }, [api, projectPath, lintCommand, testCommand, instructionsPath, architecturePath, flash]);

  if (!projectPath) {
    return null;
  }

  return (
    <div className="project-settings">
      <button
        className="project-settings-toggle"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? "▼" : "▶"} Project Settings
        {settings?.project_type && (
          <span className="project-type">({settings.project_type})</span>
        )}
        {status && <span className="save-status">{status}</span>}
      </button>

      {expanded && (
        <div className="project-settings-content">
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
              Agent Instructions Path
              <input
                type="text"
                value={instructionsPath}
                onChange={(e) => setInstructionsPath(e.target.value)}
                onBlur={handleSave}
                placeholder="e.g., AGENTS.md"
              />
            </label>
            <label>
              Architecture Docs Path
              <input
                type="text"
                value={architecturePath}
                onChange={(e) => setArchitecturePath(e.target.value)}
                onBlur={handleSave}
                placeholder="e.g., ARCHITECTURE.md"
              />
            </label>
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
