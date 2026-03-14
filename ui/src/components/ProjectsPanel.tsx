import { useState, useEffect, useCallback, useRef } from "react";
import type { ChadAPI, Session, ProjectSettings } from "chad-client";
import { AccountPicker } from "./AccountPicker.tsx";
import type { Account } from "chad-client";

interface Props {
  api: ChadAPI;
  connected: boolean;
  onOpenSession: (sessionId: string, projectPath: string) => void;
}

export function ProjectsPanel({ api, connected, onOpenSession }: Props) {
  const [projects, setProjects] = useState<ProjectSettings[]>([]);
  const [selectedProject, setSelectedProject] = useState<string | null>(null);
  const [newPath, setNewPath] = useState("");
  const [addingProject, setAddingProject] = useState(false);
  const defaultPathLoaded = useRef(false);

  // Settings editing state
  const [lintCommand, setLintCommand] = useState("");
  const [testCommand, setTestCommand] = useState("");
  const [instructionsPaths, setInstructionsPaths] = useState<string[]>([]);
  const [previewPortMode, setPreviewPortMode] = useState<"disabled" | "auto" | "manual">("disabled");
  const [previewPort, setPreviewPort] = useState("");
  const [previewCommand, setPreviewCommand] = useState("");
  const [preferredCodingAgent, setPreferredCodingAgent] = useState<Account | null>(null);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  // Autoconfigure state
  const [autoconfiguring, setAutoconfiguring] = useState(false);
  const [autoconfigJobId, setAutoconfigJobId] = useState<string | null>(null);
  const [autoconfigOutput, setAutoconfigOutput] = useState<string[]>([]);
  const [codingAgent, setCodingAgent] = useState<Account | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const outputEndRef = useRef<HTMLDivElement | null>(null);
  const preferredAgentInitialized = useRef(false);

  // Task history
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);

  const flash = useCallback((msg: string) => {
    setStatus(msg);
    setTimeout(() => setStatus(null), 3000);
  }, []);

  // Load projects and pre-fill default path on connect
  const loadProjects = useCallback(async () => {
    if (!connected) return;
    try {
      const [result, prefs] = await Promise.all([
        api.listProjects(),
        !defaultPathLoaded.current ? api.getPreferences().catch(() => null) : Promise.resolve(null),
      ]);
      setProjects(result);
      if (prefs?.last_project_path && !defaultPathLoaded.current) {
        defaultPathLoaded.current = true;
        setNewPath(prefs.last_project_path);
      }
    } catch {
      // ignore
    }
  }, [api, connected]);

  useEffect(() => {
    loadProjects();
  }, [loadProjects]);

  // Load settings when project is selected
  useEffect(() => {
    if (!selectedProject || !connected) return;
    preferredAgentInitialized.current = false;
    Promise.all([
      api.getProjectSettings(selectedProject),
      api.listAccounts(),
    ]).then(([s, accountsResult]) => {
      setLintCommand(s.lint_command || "");
      setTestCommand(s.test_command || "");
      setInstructionsPaths(s.instructions_paths || []);
      setPreviewPortMode(s.preview_port_mode || "disabled");
      setPreviewPort(s.preview_port != null ? String(s.preview_port) : "");
      setPreviewCommand(s.preview_command || "");
      // Load preferred coding agent
      if (s.preferred_coding_agent) {
        const account = accountsResult.accounts.find((a) => a.name === s.preferred_coding_agent);
        setPreferredCodingAgent(account || null);
      } else {
        setPreferredCodingAgent(null);
      }
      // Mark as initialized after loading
      preferredAgentInitialized.current = true;
    }).catch(() => {});
  }, [api, selectedProject, connected]);

  // Load task history for selected project
  useEffect(() => {
    if (!selectedProject || !connected) {
      setSessions([]);
      return;
    }
    setSessionsLoading(true);
    api.listSessions(selectedProject).then((result) => {
      setSessions(result.sessions);
    }).catch(() => {
      setSessions([]);
    }).finally(() => {
      setSessionsLoading(false);
    });
  }, [api, selectedProject, connected]);

  // Auto-save when preferred coding agent changes (after initial load)
  useEffect(() => {
    if (!selectedProject || !connected || !preferredAgentInitialized.current) return;
    const parsedPort = previewPort.trim() ? parseInt(previewPort, 10) : null;
    api.setProjectSettings({
      project_path: selectedProject,
      lint_command: lintCommand || null,
      test_command: testCommand || null,
      instructions_paths: instructionsPaths.filter(p => p.trim()),
      preview_port_mode: previewPortMode,
      preview_port: (parsedPort != null && !isNaN(parsedPort)) ? parsedPort : null,
      preview_command: previewCommand || null,
      preferred_coding_agent: preferredCodingAgent?.name || null,
    }).then(() => flash("Saved")).catch(() => flash("Error saving"));
  }, [preferredCodingAgent]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-scroll autoconfigure output
  useEffect(() => {
    outputEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [autoconfigOutput]);

  // Clean up poll on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const handleAddProject = useCallback(async () => {
    const path = newPath.trim();
    if (!path) return;
    setAddingProject(true);
    try {
      await api.setProjectSettings({ project_path: path });
      await loadProjects();
      setSelectedProject(path);
      setNewPath("");
    } catch {
      flash("Failed to add project");
    } finally {
      setAddingProject(false);
    }
  }, [api, newPath, loadProjects, flash]);

  const handleDeleteProject = useCallback(async (path: string) => {
    try {
      await api.deleteProject(path);
      if (selectedProject === path) {
        setSelectedProject(null);
      }
      await loadProjects();
    } catch {
      flash("Failed to remove project");
    }
  }, [api, selectedProject, loadProjects, flash]);

  const handleSave = useCallback(async () => {
    if (!selectedProject) return;
    setSaving(true);
    try {
      const parsedPort = previewPort.trim() ? parseInt(previewPort, 10) : null;
      await api.setProjectSettings({
        project_path: selectedProject,
        lint_command: lintCommand || null,
        test_command: testCommand || null,
        instructions_paths: instructionsPaths.filter(p => p.trim()),
        preview_port_mode: previewPortMode,
        preview_port: (parsedPort != null && !isNaN(parsedPort)) ? parsedPort : null,
        preview_command: previewCommand || null,
        preferred_coding_agent: preferredCodingAgent?.name || null,
      });
      flash("Saved");
    } catch {
      flash("Error saving");
    } finally {
      setSaving(false);
    }
  }, [api, selectedProject, lintCommand, testCommand, instructionsPaths, previewPortMode, previewPort, previewCommand, preferredCodingAgent, flash]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const handleAutoconfigure = useCallback(async () => {
    if (!codingAgent || !selectedProject) return;
    setAutoconfiguring(true);
    setAutoconfigOutput([]);

    try {
      const { job_id } = await api.startAutoconfigure(selectedProject, codingAgent.name);
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
            const updated = await api.setProjectSettings({
              project_path: selectedProject,
              lint_command: result.settings.lint_command,
              test_command: result.settings.test_command,
              instructions_paths: result.settings.instructions_paths,
              preview_port_mode: result.settings.preview_port_mode,
              preview_port: result.settings.preview_port,
              preview_command: result.settings.preview_command,
            });
            setLintCommand(updated.lint_command || "");
            setTestCommand(updated.test_command || "");
            setInstructionsPaths(updated.instructions_paths || []);
            setPreviewPortMode(updated.preview_port_mode || "disabled");
            setPreviewPort(updated.preview_port != null ? String(updated.preview_port) : "");
            setPreviewCommand(updated.preview_command || "");
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
  }, [api, selectedProject, codingAgent, stopPolling, flash]);

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

  const handleResumeTask = useCallback(async (session: Session) => {
    onOpenSession(session.id, session.project_path || selectedProject || "");
  }, [onOpenSession, selectedProject]);

  const addInstructionsPath = useCallback(() => {
    setInstructionsPaths(prev => [...prev, ""]);
  }, []);

  const removeInstructionsPath = useCallback((index: number) => {
    setInstructionsPaths(prev => prev.filter((_, i) => i !== index));
  }, []);

  const updateInstructionsPath = useCallback((index: number, value: string) => {
    setInstructionsPaths(prev => prev.map((p, i) => i === index ? value : p));
  }, []);

  const selectedSettings = projects.find(p => p.project_path === selectedProject);
  const isUnconfigured = selectedSettings && !selectedSettings.lint_command && !selectedSettings.test_command
    && selectedSettings.instructions_paths.length === 0;

  if (!connected) {
    return (
      <div className="projects-panel">
        <h2>Projects</h2>
        <div className="projects-empty">Connect to a server to manage projects.</div>
      </div>
    );
  }

  return (
    <div className="projects-panel">
      <div className="projects-layout">
        {/* Project List */}
        <div className="projects-sidebar">
          <h3>Projects</h3>
          <div className="projects-list">
            {projects.map((p) => (
              <div
                key={p.project_path}
                className={`project-item ${p.project_path === selectedProject ? "selected" : ""}`}
                onClick={() => setSelectedProject(p.project_path)}
              >
                <div className="project-item-path">{p.project_path}</div>
                {p.project_type && p.project_type !== "unknown" && (
                  <span className="project-type-badge">{p.project_type}</span>
                )}
                <button
                  className="project-remove-btn"
                  onClick={(e) => { e.stopPropagation(); handleDeleteProject(p.project_path); }}
                  title="Remove project"
                >
                  x
                </button>
              </div>
            ))}
            {projects.length === 0 && (
              <div className="projects-empty">
                No projects configured. Add a project path below to get started.
              </div>
            )}
          </div>
          <div className="projects-add">
            <input
              type="text"
              value={newPath}
              onChange={(e) => setNewPath(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleAddProject()}
              placeholder="/path/to/project"
            />
            <button onClick={handleAddProject} disabled={addingProject || !newPath.trim()}>
              {addingProject ? "Adding..." : "Add"}
            </button>
          </div>
        </div>

        {/* Project Detail */}
        <div className="projects-detail">
          {!selectedProject ? (
            <div className="projects-detail-empty">
              {projects.length > 0 ? "Select a project to view its settings and task history." : "Add a project to get started."}
            </div>
          ) : (
            <>
              <h3>{selectedProject}</h3>
              {status && <span className="save-status">{status}</span>}

              {/* Settings */}
              <div className="project-detail-section">
                <h4>Settings</h4>

                {/* Autoconfigure overlay */}
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

                {/* Autoconfigure banner */}
                {isUnconfigured && !autoconfiguring && (
                  <div className="autoconfigure-banner">
                    Project not yet configured.
                    <div className="autoconfigure-inline">
                      <AccountPicker api={api} selected={codingAgent} onSelect={setCodingAgent} placeholder="Select agent" />
                      <button
                        className="autoconfigure-btn"
                        onClick={handleAutoconfigure}
                        disabled={!codingAgent}
                      >
                        Autoconfigure
                      </button>
                    </div>
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
                    Preview
                    <select
                      value={previewPortMode}
                      onChange={(e) => {
                        setPreviewPortMode(e.target.value as "disabled" | "auto" | "manual");
                        setTimeout(handleSave, 0);
                      }}
                    >
                      <option value="disabled">Disabled</option>
                      <option value="auto">Auto-detect port</option>
                      <option value="manual">Manual port</option>
                    </select>
                  </label>
                </div>
                {previewPortMode !== "disabled" && (
                  <div className="project-settings-row">
                    <label>
                      Preview Command
                      <input
                        type="text"
                        value={previewCommand}
                        onChange={(e) => setPreviewCommand(e.target.value)}
                        onBlur={handleSave}
                        placeholder="e.g., npm run dev"
                      />
                    </label>
                    {previewPortMode === "manual" && (
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
                    )}
                  </div>
                )}

                <div className="project-settings-row">
                  <label>
                    Default Coding Agent
                    <AccountPicker
                      api={api}
                      selected={preferredCodingAgent}
                      onSelect={setPreferredCodingAgent}
                      placeholder="Use global default"
                      allowNone
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

                <div className="project-settings-actions">
                  <button onClick={handleSave} disabled={saving}>
                    {saving ? "Saving..." : "Save"}
                  </button>
                  <div className="autoconfigure-inline">
                    <AccountPicker api={api} selected={codingAgent} onSelect={setCodingAgent} placeholder="Select agent" />
                    <button
                      className="autoconfigure-btn"
                      onClick={handleAutoconfigure}
                      disabled={autoconfiguring || !codingAgent}
                      title="Use Coding Agent to discover project settings"
                    >
                      Autoconfigure
                    </button>
                  </div>
                </div>
              </div>

              {/* Task History */}
              <div className="project-detail-section">
                <h4>Previous Tasks</h4>
                {sessionsLoading ? (
                  <div className="loading">Loading tasks...</div>
                ) : sessions.length === 0 ? (
                  <div className="projects-empty">No previous tasks for this project.</div>
                ) : (
                  <div className="task-history-list">
                    {sessions.map((session) => (
                      <div
                        key={session.id}
                        className={`task-history-item ${session.status}`}
                        onClick={() => handleResumeTask(session)}
                      >
                        <div className="task-history-header">
                          <span className={`task-status-badge ${session.status}`}>
                            {session.status === "completed" ? "\u2713" : session.status === "active" ? "\u25CF" : "\u25CB"}
                          </span>
                          <span className="task-history-description">
                            {session.task_description || "(no description)"}
                          </span>
                        </div>
                        <div className="task-history-meta">
                          {session.coding_account && <span>Agent: {session.coding_account}</span>}
                          <span>{new Date(session.last_activity).toLocaleString()}</span>
                          {session.has_changes && <span className="badge changes-badge">Has changes</span>}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
