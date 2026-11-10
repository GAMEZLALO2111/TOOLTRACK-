import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { KIT_STATE_LABEL } from "../labels";
import { go, type Route } from "../router";
import type { ManufacturingMachineOption, ManufacturingWorkspace } from "../types";
import { usePoll } from "../usePoll";

const COMPONENT_LABELS = { punzon: "Punch", matriz: "Die" };
const COMPONENT_STATE_LABELS = {
  installed: "Installed and compatible",
  missing: "No physical tool assigned",
  elsewhere: "Assigned tool is elsewhere",
  incompatible: "Mounted tool does not match requirement",
};

function formatNumber(value: number | null, digits = 1) {
  return value == null ? "—" : value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function ComponentCard({ component }: { component: ManufacturingWorkspace["stations"][number]["components"][number] }) {
  return (
    <div className={`manufacturing-component manufacturing-${component.state}`}>
      <div className="manufacturing-component-title">
        <strong>{COMPONENT_LABELS[component.category]}</strong>
        <span className={`chip ${component.state === "installed" ? "st-onmachine" : "st-missing"}`}>
          {COMPONENT_STATE_LABELS[component.state]}
        </span>
      </div>
      <div className="manufacturing-component-details">
        <span>TN {component.tn}</span>
        <span>Physical ID: {component.tool_id ?? "—"}</span>
        {component.clearance && (
          <span>
            Clearance: {component.clearance}
            {component.clearance_valid !== null && ` · ${component.clearance_valid ? "within range" : "out of range"}`}
          </span>
        )}
        {component.tool_status && <span>Condition: {component.tool_status}</span>}
        {component.state === "elsewhere" && <span>Location: {component.location ?? "Unknown"}</span>}
      </div>
    </div>
  );
}

export function ManufacturingPage({ route }: { route: Route }) {
  const { session } = useAuth();
  const [machines, setMachines] = useState<ManufacturingMachineOption[] | null>(null);
  const [selectedCode, setSelectedCode] = useState(route.query.get("machine") ?? "");
  const [workspace, setWorkspace] = useState<ManufacturingWorkspace | null>(null);
  const [machinesError, setMachinesError] = useState<string | null>(null);
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const loadMachines = useCallback(async () => {
    try {
      setMachines(await api<ManufacturingMachineOption[]>("GET", "/workflow/manufacturing/machines"));
      setMachinesError(null);
    } catch (caught) {
      setMachinesError(caught instanceof ApiError ? caught.message : "Could not load machines.");
    }
  }, []);
  usePoll(loadMachines, 30000);

  const activeMachines = useMemo(() => machines ?? [], [machines]);
  const selectedMachine = activeMachines.find((machine) => machine.codigo === selectedCode) ?? null;

  useEffect(() => {
    if (!activeMachines.length) return;
    if (!activeMachines.some((machine) => machine.codigo === selectedCode)) {
      setSelectedCode(activeMachines[0].codigo);
    }
  }, [activeMachines, selectedCode]);

  useEffect(() => {
    if (!selectedCode || !selectedMachine) {
      setWorkspace(null);
      return;
    }
    let active = true;
    const loadWorkspace = async () => {
      try {
        const result = await api<ManufacturingWorkspace>(
          "GET",
          `/workflow/manufacturing/machines/${encodeURIComponent(selectedCode)}`,
        );
        if (active) {
          setWorkspace(result);
          setWorkspaceError(null);
          setUpdatedAt(new Date());
        }
      } catch (caught) {
        if (active) {
          setWorkspaceError(caught instanceof ApiError ? caught.message : "Could not load the machine workspace.");
        }
      }
    };
    setWorkspace(null);
    setWorkspaceError(null);
    void loadWorkspace();
    const timer = window.setInterval(() => void loadWorkspace(), 30000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [selectedCode, selectedMachine]);

  return (
    <section className="manufacturing-page">
      <div className="board-bar">
        <div>
          <h1>Manufacturing</h1>
          <p className="muted">Machine setup, part data, station requirements, and physical tool coverage.</p>
        </div>
        <div className="bar-right">
          {updatedAt && <span className="muted">Updated {updatedAt.toLocaleTimeString()}</span>}
          <button className="btn" onClick={() => void loadMachines()}>Refresh</button>
        </div>
      </div>

      {machinesError && <div className="note note-error">{machinesError}</div>}
      <div className="manufacturing-selector info-card">
        <label htmlFor="manufacturing-machine">Machine</label>
        <select
          id="manufacturing-machine"
          value={selectedCode}
          onChange={(event) => {
            setSelectedCode(event.target.value);
            go("/manufacturing", { machine: event.target.value });
          }}
          disabled={!activeMachines.length}
        >
          {!activeMachines.length && <option value="">No active machines available</option>}
          {activeMachines.map((machine) => (
            <option key={machine.codigo} value={machine.codigo}>
              {machine.codigo} · {machine.nombre}
            </option>
          ))}
        </select>
      </div>

      {!machines && !machinesError && <p className="muted">Loading machines…</p>}
      {machines && !activeMachines.length && <div className="note note-info">No active machines are registered.</div>}
      {workspaceError && <div className="note note-error">{workspaceError}</div>}
      {selectedMachine && workspace && (
        <>
          <div className="manufacturing-machine-head info-card">
            <div>
              <span className="eyebrow">Selected machine</span>
              <h2>{workspace.machine} · {workspace.machine_name}</h2>
              <div className="muted">
                {[workspace.machine_type, workspace.building, workspace.line].filter(Boolean).join(" · ") || "Machine profile details not configured"}
              </div>
            </div>
            <div className="manufacturing-part">
              <span className="eyebrow">Part number from active setup</span>
              <strong>{workspace.part_number ?? "No active setup registered"}</strong>
              {workspace.setup?.drawing_name && <span className="muted">{workspace.setup.drawing_name}</span>}
            </div>
          </div>

          <div className="note note-info">
            This shows the registered active setup and physical tool locations. It does not confirm production status from MES.
          </div>

          <div className="manufacturing-summary">
            <article className="info-card">
              <span className="eyebrow">Physical coverage</span>
              <strong className={workspace.coverage.complete ? "coverage-complete" : "coverage-incomplete"}>
                {workspace.coverage.installed} / {workspace.coverage.required}
              </strong>
              <span className="muted">
                {workspace.coverage.required === 0
                  ? "No setup requirements"
                  : workspace.coverage.complete
                    ? "All required components are recorded on this machine"
                    : "One or more required components are missing, elsewhere, or incompatible"}
              </span>
            </article>
            <article className="info-card">
              <span className="eyebrow">Setup technical data</span>
              <strong>{workspace.setup?.material ?? workspace.program?.material ?? "Material not recorded"}</strong>
              <span>
                Thickness: {workspace.setup?.thickness ?? workspace.program?.thickness ?? "—"}
                {workspace.setup?.thickness || workspace.program?.thickness ? " in" : ""}
              </span>
              {workspace.setup && (
                <span className="muted">
                  Setup recorded {new Date(`${workspace.setup.created_at}Z`).toLocaleString()}
                </span>
              )}
            </article>
            <article className="info-card">
              <span className="eyebrow">Part program</span>
              {workspace.program ? (
                <>
                  <strong>Revision {workspace.program.revision}</strong>
                  <span>{[workspace.program.family, workspace.program.program_type].filter(Boolean).join(" · ")}</span>
                  <span>{workspace.program.drawing_name ?? "Drawing not recorded"} · {workspace.program.file_name ?? "File not recorded"}</span>
                  <span className="muted">
                    Updated {new Date(`${workspace.program.updated_at}Z`).toLocaleString()} by {workspace.program.updated_by}
                  </span>
                  <a className="manufacturing-link" href="#/programs">Open part program details</a>
                </>
              ) : (
                <span className="muted">No matching program record for this machine type and part number.</span>
              )}
            </article>
            {workspace.program && (
              <article className="info-card">
                <span className="eyebrow">Planning reference</span>
                <strong>{formatNumber(workspace.program.planning.theoretical_uph)} theoretical UPH</strong>
                <span>
                  {formatNumber(workspace.program.planning.pieces_per_blank, 0)} pieces / blank ·{" "}
                  {formatNumber(workspace.program.planning.cycle_with_lead_s)} s cycle with lead
                </span>
              </article>
            )}
          </div>

          <div className="manufacturing-section-head">
            <h2>Station layout and required tools</h2>
            <span className="muted">{workspace.stations.length} required stations</span>
          </div>
          {!workspace.stations.length ? (
            <div className="info-card muted">The active setup has no station requirements recorded.</div>
          ) : (
            <div className="manufacturing-stations">
              {workspace.stations.map((station) => (
                <article className="info-card manufacturing-station" key={station.station}>
                  <header>
                    <div>
                      <span className="eyebrow">Station</span>
                      <h3>{station.station}</h3>
                    </div>
                    <div className="manufacturing-station-meta">
                      <span>TN {station.tn}</span>
                      <span>Angle {station.angle || "—"}</span>
                      {station.machine_station?.tamano && <span>Size {station.machine_station.tamano}</span>}
                      {station.machine_station?.posicion && <span>{station.machine_station.posicion}</span>}
                    </div>
                  </header>
                  <div className="manufacturing-components">
                    {station.components.map((component) => (
                      <ComponentCard key={component.category} component={component} />
                    ))}
                  </div>
                </article>
              ))}
            </div>
          )}

          {workspace.program && (
            <>
              <div className="manufacturing-section-head">
                <h2>Part program requirements</h2>
                <span className="muted">Saved program reference · revision {workspace.program.revision}</span>
              </div>
              {workspace.program.requirements.length ? (
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr><th>Station</th><th>TN</th><th>Angle</th></tr>
                    </thead>
                    <tbody>
                      {workspace.program.requirements.map((requirement) => (
                        <tr key={`${requirement.station}-${requirement.tn}`}>
                          <td>{requirement.station}</td>
                          <td><strong>{requirement.tn}</strong></td>
                          <td>{requirement.angle ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="muted">The saved program has no station requirements.</p>
              )}
            </>
          )}

          <div className="manufacturing-section-head">
            <h2>Tools pending pickup</h2>
            <span className="muted">{workspace.pending_pickups.length}</span>
          </div>
          {workspace.pending_pickups.length ? (
            <div className="info-card manufacturing-pickups">
              {workspace.pending_pickups.map((item) => (
                <div key={item.tool_id}>
                  <strong>{item.tool_id}</strong>
                  <span>TN {item.tn ?? "—"} · {item.category ?? "Tool"}</span>
                  <span className="muted">{new Date(`${item.created_at}Z`).toLocaleString()}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted">No tools are currently pending pickup from this machine.</p>
          )}

          <div className="manufacturing-section-head">
            <h2>Active kitting status</h2>
          </div>
          {workspace.active_kit ? (
            <div className="info-card manufacturing-active-kit">
              <strong>{workspace.active_kit.id}</strong>
              <span className="chip">{KIT_STATE_LABEL[workspace.active_kit.state] ?? workspace.active_kit.state}</span>
              <span>Started {new Date(`${workspace.active_kit.created_at}Z`).toLocaleString()}</span>
              <span className="muted">Updated {new Date(`${workspace.active_kit.updated_at}Z`).toLocaleString()}</span>
            </div>
          ) : (
            <p className="muted">No active kit is registered for this machine.</p>
          )}
          <div className="note note-info">
            Service requisitions and their history are not available yet; they will appear here after that workflow is implemented.
          </div>
        </>
      )}
      {selectedMachine && !workspace && !workspaceError && <p className="muted">Loading machine setup…</p>}
      {session && (
        <p className="manufacturing-user muted">
          Signed in as {session.fullName || session.username}
          {session.employeeNumber && ` · Employee ${session.employeeNumber}`}
        </p>
      )}
    </section>
  );
}
