import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { Dialog, ErrorNote } from "../components/Dialog";
import { Chip } from "../components/MachineCard";
import { ReasonDialog } from "../components/ReasonDialog";
import { ScanInput } from "../components/ScanInput";
import { locationCode, type Capture } from "../scan";
import { canReceive, IncidentFields, incidentMissing, positionLabel, problemLabel, RequestChip, when, type IncidentValues } from "../components/Service";
import { KIT_STATE_LABEL, PROGRAM_PROBLEMS, SERVICE_TYPE_LABEL, SERVICE_TYPES, TOOL_STATE_CLASS, TOOL_STATE_LABEL } from "../labels";
import { go, type Route } from "../router";
import type { ManufacturingMachineOption, ManufacturingWorkspace, ServiceRequest } from "../types";
import { usePoll } from "../usePoll";
import { useBoard } from "../useBoard";
import { MACHINE_KEY, MachineOperatorBar, requestNextPart, useOnMachine } from "../components/MachineOperatorBar";
import { QueueDialog } from "../components/QueueDialog";
import { fmt } from "./ProgramUpload";

type Station = ManufacturingWorkspace["stations"][number];
type Component = Station["components"][number];

const CATEGORY_NAME = { punzon: "Punch", matriz: "Die" };

function Fact({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div>
      <div className="muted small">{label}</div>
      <strong>{value === null || value === undefined || value === "" ? "—" : value}</strong>
    </div>
  );
}

interface ToolCellProps {
  component: Component;
  canRequest: boolean;
  onRequest: () => void;
  onRegister: () => void;
  onReceive: (request: ServiceRequest) => void;
}

function ToolCell({ component, canRequest, onRequest, onRegister, onReceive }: ToolCellProps) {
  const request = component.open_request;
  if (request) {
    return (
      <div className="tool-cell">
        <RequestChip request={request} />
        <span className="muted small">
          {request.folio} · {SERVICE_TYPE_LABEL[request.type] ?? request.type}
          {request.tool_id ? ` · ${request.tool_id}` : ""}
        </span>
        {canRequest && canReceive(request) && (
          <button className="btn btn-primary" onClick={() => onReceive(request)}>
            Receive tool
          </button>
        )}
      </div>
    );
  }

  if (!component.tool_id) {
    return (
      <div className="tool-cell">
        <Chip text="No tool recorded" className="st-missing" />
        {canRequest && (
          <div className="cell-actions">
            <button className="btn" onClick={onRegister}>
              Register mounted tool
            </button>
            <button className="btn" onClick={onRequest}>
              Request tool
            </button>
          </div>
        )}
      </div>
    );
  }

  const status = component.tool_status ?? "disponible";
  const details = (
    <>
      <strong>{component.tool_id}</strong>
      <span className="muted small">
        {[component.tool_type, component.tool_size && `size ${component.tool_size}`, component.clearance && `clearance ${component.clearance}`].filter(Boolean).join(" · ") || "No tool details"}
      </span>
      <span className="chips">
        <Chip text={TOOL_STATE_LABEL[status] ?? status} className={TOOL_STATE_CLASS[status] ?? ""} />
        {component.state === "incompatible" && <Chip text="Does not match" className="st-missing" />}
        {component.clearance_valid === false && <Chip text="Clearance out of range" className="st-missing" />}
        {component.state === "elsewhere" && <Chip text={`In ${component.location ?? "unknown"}`} className="st-kitting" />}
      </span>
    </>
  );

  if (canRequest && component.state !== "elsewhere") {
    return (
      <button type="button" className="tool-pick" onClick={onRequest} aria-label={`Request service for ${component.tool_id}`}>
        {details}
        <span className="tool-pick-hint">Request service…</span>
      </button>
    );
  }
  return <div className="tool-cell">{details}</div>;
}

interface RequestDialogProps {
  machine: string;
  station: Station;
  component: Component;
  onClose: () => void;
  onDone: (request: ServiceRequest) => void;
}

function RequestDialog({ machine, station, component, onClose, onDone }: RequestDialogProps) {
  const emptyPosition = !component.tool_id;
  const [type, setType] = useState(emptyPosition ? "tool_request" : "");
  const [note, setNote] = useState("");
  const { session } = useAuth();
  const [incident, setIncident] = useState<IncidentValues>({ operator: session?.username ?? "", cause: "", moment: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const chosen = SERVICE_TYPES.find((option) => option.value === type);
  const noteRequired = !!chosen?.noteRequired;
  const isDamage = type === "damage";

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!type) return setError("Choose what you need from Tool Crib.");
    if (noteRequired && !note.trim()) return setError("Describe what happened so Tool Crib knows what to expect.");
    const missing = isDamage ? incidentMissing(incident) : null;
    if (missing) return setError(missing);
    setBusy(true);
    setError(null);
    try {
      onDone(
        await api<ServiceRequest>("POST", "/workflow/service-requests", {
          machine,
          requirement_id: station.requirement_id,
          station: station.station,
          tn: station.tn,
          angle: station.angle,
          category: component.category,
          tipo: type,
          tool_id: component.tool_id,
          nota: note.trim() || null,
          ...(isDamage ? { operador: incident.operator, causa: incident.cause, momento: incident.moment } : {}),
        }),
      );
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The request could not be sent.");
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={emptyPosition ? "Request a tool" : `Request service · ${component.tool_id}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Back
          </button>
          <button className="btn btn-primary" onClick={() => void submit()} disabled={busy || !type}>
            {busy ? "Sending…" : "Send to Tool Crib"}
          </button>
        </>
      }
    >
      <form className="form" onSubmit={(event) => void submit(event)}>
        <div className="facts">
          <Fact label="Machine" value={machine} />
          <Fact label="Position" value={`Station ${station.station} · ${CATEGORY_NAME[component.category]}`} />
          <Fact label="TN" value={station.tn} />
          {component.tool_id && <Fact label="Tool on the machine" value={component.tool_id} />}
        </div>
        {!emptyPosition && (
          <>
            <div className="muted small">What do you need?</div>
            <div className="svc-options">
              {SERVICE_TYPES.map((option) => (
                <label key={option.value} className={`svc-option${type === option.value ? " selected" : ""}`}>
                  <input type="radio" name="service-type" value={option.value} checked={type === option.value} onChange={() => setType(option.value)} />
                  <span>
                    <strong>{option.label}</strong>
                  </span>
                </label>
              ))}
            </div>
            <p className="note note-warn">Take the tool off the machine first. This position will show as empty until Tool Crib brings the replacement.</p>
          </>
        )}
        {isDamage && <IncidentFields values={incident} onChange={setIncident} />}
        {type && (
          <label>
            {noteRequired ? "What happened? (required)" : "Notes (optional)"}
            <textarea rows={3} maxLength={500} value={note} onChange={(event) => setNote(event.target.value)} autoFocus />
          </label>
        )}
        <ErrorNote message={error} />
      </form>
    </Dialog>
  );
}

function RegisterDialog({ machine, station, component, onClose, onDone }: RequestDialogProps & { onDone: () => void }) {
  const [toolId, setToolId] = useState("");
  const [capture, setCapture] = useState<Capture>("manual");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!toolId.trim()) return setError("Scan or type the ID of the tool mounted on this position.");
    setBusy(true);
    setError(null);
    try {
      await api("POST", `/workflow/manufacturing/machines/${encodeURIComponent(machine)}/mounted`, {
        requirement_id: station.requirement_id,
        station: station.station,
        tn: station.tn,
        angle: station.angle,
        category: component.category,
        tool_id: toolId,
        captura: capture,
      });
      onDone();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The tool could not be registered.");
      setBusy(false);
    }
  }

  return (
    <Dialog
      title="Register mounted tool"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Back
          </button>
          <button className="btn btn-primary" onClick={() => void submit()} disabled={busy}>
            {busy ? "Saving…" : "Register"}
          </button>
        </>
      }
    >
      <form className="form" onSubmit={(event) => void submit(event)}>
        <div className="facts">
          <Fact label="Machine" value={machine} />
          <Fact label="Position" value={`Station ${station.station} · ${CATEGORY_NAME[component.category]}`} />
          <Fact label="Required TN" value={station.tn} />
        </div>
        <ScanInput className="big-input" value={toolId} onValue={(value, mode) => { setToolId(value); setCapture(mode); }} placeholder="Scan the tool, or type its ID" autoFocus aria-label="Tool ID" />
        <ErrorNote message={error} />
      </form>
    </Dialog>
  );
}

function ProgramProblemDialog({ workspace, onClose, onDone }: { workspace: ManufacturingWorkspace; onClose: () => void; onDone: (request: ServiceRequest) => void }) {
  const [problem, setProblem] = useState("");
  const [station, setStation] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stations = [...new Set(workspace.stations.map((row) => row.station))];

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!problem) return setError("Choose what is wrong with the program.");
    if (problem === "other" && !note.trim()) return setError("Describe the problem.");
    setBusy(true);
    setError(null);
    try {
      onDone(
        await api<ServiceRequest>("POST", "/workflow/service-requests/program-problem", {
          machine: workspace.machine,
          problema: problem,
          station: station || null,
          nota: note.trim() || null,
        }),
      );
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The report could not be sent.");
      setBusy(false);
    }
  }

  return (
    <Dialog
      title="Report a program problem"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Back
          </button>
          <button className="btn btn-primary" onClick={() => void submit()} disabled={busy || !problem}>
            {busy ? "Sending…" : "Send"}
          </button>
        </>
      }
    >
      <form className="form" onSubmit={(event) => void submit(event)}>
        <div className="facts">
          <Fact label="Machine" value={workspace.machine} />
          <Fact label="Part number" value={workspace.part_number} />
        </div>
        <div className="muted small">What is wrong?</div>
        <div className="svc-options">
          {PROGRAM_PROBLEMS.map((option) => (
            <label key={option.value} className={`svc-option${problem === option.value ? " selected" : ""}`}>
              <input type="radio" name="program-problem" value={option.value} checked={problem === option.value} onChange={() => setProblem(option.value)} />
              <span>
                <strong>{option.label}</strong>
              </span>
            </label>
          ))}
        </div>
        {stations.length > 0 && (
          <label>
            Station (optional)
            <select value={station} onChange={(event) => setStation(event.target.value)}>
              <option value="">Not a specific station</option>
              {stations.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          {problem === "other" ? "Describe the problem (required)" : "Notes (optional)"}
          <textarea rows={3} maxLength={500} value={note} onChange={(event) => setNote(event.target.value)} />
        </label>
        <ErrorNote message={error} />
      </form>
    </Dialog>
  );
}

function ReceiveDialog({ request, onClose, onDone }: { request: ServiceRequest; onClose: () => void; onDone: (request: ServiceRequest) => void }) {
  const [toolId, setToolId] = useState("");
  const [capture, setCapture] = useState<Capture>("manual");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!toolId.trim()) return setError("Scan or type the ID of the tool you received.");
    setBusy(true);
    setError(null);
    try {
      onDone(await api<ServiceRequest>("POST", `/workflow/service-requests/${request.id}/install`, { tool_id: toolId, captura: capture }));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The installation could not be recorded.");
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={`Receive tool · ${request.folio}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Back
          </button>
          <button className="btn btn-primary" onClick={() => void submit()} disabled={busy}>
            {busy ? "Saving…" : "Installed on the machine"}
          </button>
        </>
      }
    >
      <form className="form" onSubmit={(event) => void submit(event)}>
        <div className="facts">
          <Fact label="Machine" value={request.machine} />
          <Fact label="Position" value={positionLabel(request)} />
          {request.tool_id && <Fact label="Tool removed" value={request.tool_id} />}
        </div>
        <ScanInput className="big-input" value={toolId} onValue={(value, mode) => { setToolId(value); setCapture(mode); }} placeholder="Scan the tool, or type its ID" autoFocus aria-label="Tool ID" />
        <ErrorNote message={error} />
      </form>
    </Dialog>
  );
}

export function ManufacturingPage({ route }: { route: Route }) {
  const { session, can } = useAuth();
  const canRequest = can("service.request");
  const [machines, setMachines] = useState<ManufacturingMachineOption[] | null>(null);
  const [selectedCode, setSelectedCode] = useState(route.query.get("machine") ?? localStorage.getItem(MACHINE_KEY) ?? "");
  const [workspace, setWorkspace] = useState<ManufacturingWorkspace | null>(null);
  const [machinesError, setMachinesError] = useState<string | null>(null);
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [tick, setTick] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [requesting, setRequesting] = useState<{ station: Station; component: Component } | null>(null);
  const [registering, setRegistering] = useState<{ station: Station; component: Component } | null>(null);
  const [reportingProgram, setReportingProgram] = useState(false);
  const [receiving, setReceiving] = useState<ServiceRequest | null>(null);
  const [cancelling, setCancelling] = useState<ServiceRequest | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [machineScan, setMachineScan] = useState("");
  const [planOpen, setPlanOpen] = useState(false);
  const { board, reload: reloadBoard } = useBoard(workspace?.building ?? "B1");

  const loadMachines = useCallback(async () => {
    try {
      setMachines(await api<ManufacturingMachineOption[]>("GET", "/workflow/manufacturing/machines"));
      setMachinesError(null);
    } catch (caught) {
      setMachinesError(caught instanceof ApiError ? caught.message : "Could not load machines.");
    }
  }, []);
  usePoll(loadMachines, 60000);

  const activeMachines = useMemo(() => machines ?? [], [machines]);
  const selectedMachine = activeMachines.find((machine) => machine.codigo === selectedCode) ?? null;

  useEffect(() => {
    if (!activeMachines.length) return;
    if (!activeMachines.some((machine) => machine.codigo === selectedCode)) setSelectedCode(activeMachines[0].codigo);
  }, [activeMachines, selectedCode]);

  useEffect(() => {
    setWorkspace(null);
    setWorkspaceError(null);
  }, [selectedCode]);

  useEffect(() => {
    if (!selectedCode || !selectedMachine) return;
    let active = true;
    const loadWorkspace = async () => {
      try {
        const result = await api<ManufacturingWorkspace>("GET", `/workflow/manufacturing/machines/${encodeURIComponent(selectedCode)}`);
        if (active) {
          setWorkspace(result);
          setWorkspaceError(null);
          setUpdatedAt(new Date());
        }
      } catch (caught) {
        if (active) setWorkspaceError(caught instanceof ApiError ? caught.message : "Could not load the machine.");
      }
    };
    void loadWorkspace();
    const timer = window.setInterval(() => {
      if (!document.hidden) void loadWorkspace();
    }, 15000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [selectedCode, selectedMachine, tick]);

  function chooseMachine(code: string) {
    setSelectedCode(code);
    localStorage.setItem(MACHINE_KEY, code);
    go("/manufacturing", { machine: code });
  }

  function scanMachine() {
    const code = locationCode(machineScan);
    setMachineScan("");
    if (!code) return;
    const match = activeMachines.find((machine) => machine.codigo.toUpperCase() === code);
    if (match) {
      setActionError(null);
      chooseMachine(match.codigo);
    } else {
      setActionError(`${code} is not an active machine.`);
    }
  }

  function sent(request: ServiceRequest) {
    setRequesting(null);
    setMessage(
      request.type === "tool_request"
        ? `Tool request ${request.folio} sent to Tool Crib.`
        : `${request.folio} sent to Tool Crib. Station ${request.station} is now waiting for its tool.`,
    );
    setTick((value) => value + 1);
  }

  function programReported(request: ServiceRequest) {
    setReportingProgram(false);
    setMessage(`${request.folio} sent. Tool Crib or Engineering will take it.`);
    setTick((value) => value + 1);
  }

  function registered() {
    setMessage(`Tool registered on station ${registering?.station.station}. You can now request service on it.`);
    setRegistering(null);
    setTick((value) => value + 1);
  }

  function received(request: ServiceRequest) {
    setReceiving(null);
    const where = `${request.replacement_tool_id} installed on station ${request.station}${request.same_tool ? " (the same tool came back)" : ""}.`;
    setMessage(request.state === "closed" ? `${where} ${request.folio} is closed.` : `${where} Tool Crib keeps working on ${request.tool_id}.`);
    setTick((value) => value + 1);
  }

  async function cancel(request: ServiceRequest, reason: string) {
    setCancelling(null);
    setActionError(null);
    try {
      const result = await api<ServiceRequest>("POST", `/workflow/service-requests/${request.id}/cancel`, { motivo: reason });
      setMessage(`${request.folio} cancelled${result.restored ? "; the tool is back on its position" : ""}.`);
      setTick((value) => value + 1);
    } catch (caught) {
      setActionError(caught instanceof ApiError ? caught.message : "The request could not be cancelled.");
    }
  }

  const program = workspace?.program ?? null;
  const setup = workspace?.setup ?? null;
  const requests = workspace?.service_requests ?? [];
  const openRequests = requests.filter((request) => ["open", "attending", "collected"].includes(request.state));
  const who = session?.fullName || session?.username || "";
  const onMachine = useOnMachine(workspace?.operators);
  const planMachine = can("demand.view") ? (board?.maquinas.find((item) => item.maquina === workspace?.machine) ?? null) : null;

  async function requestKit(demandId: number) {
    if (!workspace) return;
    setActionError(null);
    try {
      await requestNextPart(workspace.machine, demandId);
      setMessage("Kit requested. Tool Crib has it in its queue.");
      setTick((value) => value + 1);
      void reloadBoard();
    } catch (caught) {
      setActionError(caught instanceof ApiError ? caught.message : "The request could not be sent.");
    }
  }

  return (
    <section className="page">
      <div className="board-bar">
        <h1>Manufacturing</h1>
        <div className="bar-right">
          {updatedAt && <span className="muted small">Updated {updatedAt.toLocaleTimeString()}</span>}
          <ScanInput
            className="scan-small"
            value={machineScan}
            onValue={setMachineScan}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                scanMachine();
              }
            }}
            placeholder="Scan machine label"
            aria-label="Scan the machine label"
          />
          <select value={selectedCode} onChange={(event) => chooseMachine(event.target.value)} disabled={!activeMachines.length} aria-label="Machine">
            {!activeMachines.length && <option value="">No active machines</option>}
            {activeMachines.map((machine) => (
              <option key={machine.codigo} value={machine.codigo}>
                {machine.codigo} · {machine.nombre}
              </option>
            ))}
          </select>
          <button className="btn" onClick={() => setTick((value) => value + 1)}>
            Refresh
          </button>
        </div>
      </div>

      <ErrorNote message={machinesError} />
      <ErrorNote message={workspaceError} />
      <ErrorNote message={actionError} />
      {message && <div className="note note-info">✔ {message}</div>}
      {!machines && !machinesError && <p className="muted">Loading machines…</p>}
      {machines && !activeMachines.length && <p className="note note-info">No active machines are registered.</p>}
      {selectedMachine && !workspace && !workspaceError && <p className="muted">Loading {selectedCode}…</p>}

      {workspace && (
        <>
          <div className="summary">
            <span className={`chip sum ${workspace.coverage.required === 0 ? "lvl-SIN_COLA" : workspace.coverage.complete ? "lvl-VERDE" : "lvl-ROJO"}`}>
              {workspace.coverage.required === 0 ? "No tools required" : `Tools ${workspace.coverage.installed}/${workspace.coverage.required}`}
            </span>
            {openRequests.length > 0 && <span className="chip sum lvl-AMARILLO">{openRequests.length} open request{openRequests.length === 1 ? "" : "s"}</span>}
            {workspace.active_kit && <span className="chip sum lvl-PARO">Kit {KIT_STATE_LABEL[workspace.active_kit.state] ?? workspace.active_kit.state}</span>}
          </div>

          <div className="panel">
            <h2>On {workspace.machine}</h2>
            <div className="facts">
              <Fact label="Machine" value={`${workspace.machine} · ${workspace.machine_name}`} />
              <Fact label="Part number" value={workspace.part_number ?? "No setup registered"} />
              <Fact label="Setup loaded" value={setup ? when(setup.created_at) : null} />
              <Fact label="Operator" value={who ? `${who}${session?.employeeNumber ? ` · ${session.employeeNumber}` : ""}` : null} />
              <Fact label="Machine type" value={workspace.machine_type} />
              <Fact label="Building · line" value={[workspace.building, workspace.line].filter(Boolean).join(" · ")} />
            </div>
            <MachineOperatorBar code={workspace.machine} operators={workspace.operators ?? []} onChanged={() => { setMessage(null); setTick((value) => value + 1); void reloadBoard(); }} />
            {can("machine.operate") && !onMachine && (
              <p className="note note-warn">Take this machine to record its produced pieces and to request its next part number.</p>
            )}
          </div>

          {planMachine && (
            <div className="panel">
              <div className="machine-section-head" style={{ margin: 0 }}>
                <h2>Plan of {workspace.machine}</h2>
                <button className="btn" onClick={() => setPlanOpen(true)}>
                  Full plan{onMachine ? " and produced pieces" : ""}
                </button>
              </div>
              {planMachine.demandas.filter((d) => d.pendiente > 0).slice(0, 4).map((demand) => (
                <div key={demand.id} className="plan-line">
                  <strong>{demand.numero_parte}</strong>
                  {demand.orden_trabajo && <span className="plan-row-wo"> · WO {demand.orden_trabajo}</span>}
                  <span className="muted">
                    {" "}
                    · {demand.piezas_producidas}/{demand.cantidad} pcs{demand.hr_pendiente !== null ? ` · ${demand.hr_pendiente.toFixed(1)} h left` : ""}
                  </span>{" "}
                  {demand.es_actual && <span className="chip tag-dark">RUNNING</span>}
                  {demand.es_siguiente && <span className="chip tag-slate">NEXT</span>}
                  {demand.solicitud_parte && <span className="chip st-kitting">Kit requested</span>}
                  {onMachine && demand.estado === "NO INICIADO" && !demand.solicitud_parte && (
                    <button className="btn btn-primary" onClick={() => void requestKit(demand.id)}>
                      Request kit
                    </button>
                  )}
                </div>
              ))}
              {planMachine.demandas.filter((d) => d.pendiente > 0).length === 0 && <p className="muted">Nothing is planned for this machine.</p>}
            </div>
          )}

          {workspace.part_number && (
            <div className="panel">
              <div className="machine-section-head" style={{ margin: 0 }}>
                <h2>Part number {workspace.part_number}</h2>
                {canRequest && (
                  <button className="btn" onClick={() => { setMessage(null); setReportingProgram(true); }}>
                    Report program problem
                  </button>
                )}
              </div>
              <div className="facts">
                <Fact label="Client" value={program?.client} />
                <Fact label="Family" value={program?.family} />
                <Fact label="Program type" value={program?.program_type} />
                <Fact label="Revision" value={program?.revision} />
                <Fact label="Drawing" value={program?.drawing_name ?? setup?.drawing_name} />
                <Fact label="Material" value={program?.material ?? setup?.material} />
                <Fact label="Thickness (in)" value={program?.thickness ?? setup?.thickness} />
                <Fact label="Program file" value={program?.file_name} />
                <Fact label="Pieces per blank" value={fmt(program?.planning.pieces_per_blank)} />
                <Fact label="Cycle (s per blank)" value={fmt(program?.planning.cycle_time_s)} />
                <Fact label="Cycle + lead time (s)" value={fmt(program?.planning.cycle_with_lead_s)} />
                <Fact label="Theoretical UPH" value={fmt(program?.planning.theoretical_uph)} />
              </div>
              {program ? (
                <p className="muted small">
                  Program updated {when(program.updated_at)} by {program.updated_by}.
                </p>
              ) : (
                <p className="muted small">No program is registered for this part number and machine type; showing what the setup records.</p>
              )}
            </div>
          )}

          <div className="machine-section-head">
            <div>
              <h2>Stations and tools</h2>
              {!canRequest && <p className="muted small">Your role can view the tools but not send requests.</p>}
            </div>
          </div>
          {!workspace.stations.length ? (
            <p className="note note-info">
              {workspace.setup
                ? "There are no station requirements for this part number yet. Ask Engineering to load its program."
                : "There is no setup registered on this machine."}
            </p>
          ) : (
            <>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Station</th>
                      <th>TN</th>
                      <th>Angle</th>
                      <th className="hide-sm">Size · position</th>
                      <th>Punch</th>
                      <th>Die</th>
                    </tr>
                  </thead>
                  <tbody>
                    {workspace.stations.map((station) => (
                      <tr key={station.requirement_id ?? `${station.station}-${station.tn}-${station.angle}`}>
                        <td>
                          <strong>{station.station}</strong>
                        </td>
                        <td>{station.tn}</td>
                        <td>{station.angle || "—"}</td>
                        <td className="hide-sm muted">{[station.machine_station?.size, station.machine_station?.position].filter(Boolean).join(" · ") || "—"}</td>
                        {station.components.map((component) => (
                          <td key={component.category}>
                            <ToolCell
                              component={component}
                              canRequest={canRequest}
                              onRequest={() => {
                                setMessage(null);
                                setRequesting({ station, component });
                              }}
                              onRegister={() => {
                                setMessage(null);
                                setRegistering({ station, component });
                              }}
                              onReceive={setReceiving}
                            />
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          <div className="machine-section-head">
            <h2>Requests for {workspace.machine}</h2>
          </div>
          {requests.length === 0 ? (
            <p className="note note-info">Nothing has been requested from Tool Crib for this machine yet.</p>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Request</th>
                    <th>Position</th>
                    <th className="hide-sm">Requested</th>
                    <th>Status</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {requests.map((request) => {
                    const done = request.state === "closed" || request.state === "cancelled";
                    return (
                      <tr key={request.id} className={done ? "inactive" : ""}>
                        <td>
                          <strong>{request.folio}</strong>
                          <div className="muted small">{SERVICE_TYPE_LABEL[request.type] ?? request.type}</div>
                        </td>
                        <td>
                          {positionLabel(request)}
                          <div className="muted small">
                            {request.type === "program_problem"
                              ? problemLabel(request.problem)
                              : `${request.tool_id ?? "No tool"}${request.replacement_tool_id ? ` → ${request.replacement_tool_id}` : ""}`}
                          </div>
                        </td>
                        <td className="hide-sm">
                          {request.requested_by_name}
                          <div className="muted small">{when(request.requested_at)}</div>
                        </td>
                        <td>
                          <RequestChip request={request} />
                          <div className="muted small">
                            {request.state === "attending" && `${request.attended_by_name} is on it`}
                            {request.state === "collected" && `Tool recovered by ${request.collected_by}`}
                            {request.state === "closed" && (request.resolution ? `${request.resolution} · ${when(request.closed_at)}` : when(request.closed_at))}
                            {request.state === "cancelled" && (request.cancel_reason ?? "")}
                          </div>
                        </td>
                        <td className="row-actions">
                          {canRequest && canReceive(request) && (
                            <button className="btn btn-primary" onClick={() => setReceiving(request)}>
                              Receive tool
                            </button>
                          )}
                          {canRequest && request.state === "open" && request.requested_by === session?.username && (
                            <button className="btn btn-ghost-danger" onClick={() => setCancelling(request)}>
                              Cancel
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {workspace.pending_pickups.length > 0 && (
            <>
              <div className="machine-section-head">
                <h2>Tools waiting to be picked up</h2>
              </div>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Tool</th>
                      <th>TN</th>
                      <th>Since</th>
                    </tr>
                  </thead>
                  <tbody>
                    {workspace.pending_pickups.map((item) => (
                      <tr key={item.tool_id}>
                        <td>
                          <strong>{item.tool_id}</strong>
                        </td>
                        <td>{item.tn ?? "—"}</td>
                        <td>{when(item.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}

      {requesting && workspace && (
        <RequestDialog machine={workspace.machine} station={requesting.station} component={requesting.component} onClose={() => setRequesting(null)} onDone={sent} />
      )}
      {reportingProgram && workspace && <ProgramProblemDialog workspace={workspace} onClose={() => setReportingProgram(false)} onDone={programReported} />}
      {registering && workspace && (
        <RegisterDialog machine={workspace.machine} station={registering.station} component={registering.component} onClose={() => setRegistering(null)} onDone={registered} />
      )}
      {receiving && <ReceiveDialog request={receiving} onClose={() => setReceiving(null)} onDone={received} />}
      {planOpen && planMachine && (
        <QueueDialog
          machine={planMachine}
          marks={board?.marcas ?? []}
          onClose={() => setPlanOpen(false)}
          onChanged={() => {
            void reloadBoard();
            setTick((value) => value + 1);
          }}
          onKit={() => undefined}
          onAdd={() => undefined}
          onEdit={() => undefined}
        />
      )}
      {cancelling && (
        <ReasonDialog
          title={`Cancel ${cancelling.folio}`}
          message="Tool Crib has not taken it yet. If the tool is still on the machine it goes back to its position."
          confirmLabel="Cancel request"
          danger
          onConfirm={(reason) => void cancel(cancelling, reason)}
          onClose={() => setCancelling(null)}
        />
      )}
    </section>
  );
}
