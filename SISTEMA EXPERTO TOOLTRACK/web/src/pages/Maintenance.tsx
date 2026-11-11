import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { Dialog, ErrorNote } from "../components/Dialog";
import { Chip } from "../components/MachineCard";
import { ScanInput } from "../components/ScanInput";
import { causeLabel, IncidentFields, incidentMissing, momentLabel, when, type IncidentValues } from "../components/Service";
import { DAMAGE_CAUSES, TICKET_STATE_CLASS, TICKET_STATE_LABEL, TOOL_STATE_LABEL } from "../labels";
import { sameCode, toolCode, type Capture } from "../scan";
import type { LocationRow, MaintenanceTicket } from "../types";
import { usePoll } from "../usePoll";

const VIEWS = [
  { value: "active", label: "Open" },
  { value: "closed", label: "Closed" },
  { value: "all", label: "All" },
];

function Footer({ busy, label, onClose, onSubmit, danger = false }: { busy: boolean; label: string; onClose: () => void; onSubmit: () => void; danger?: boolean }) {
  return (
    <>
      <button className="btn" onClick={onClose} disabled={busy}>
        Back
      </button>
      <button className={`btn ${danger ? "btn-danger" : "btn-primary"}`} onClick={onSubmit} disabled={busy}>
        {busy ? "Saving…" : label}
      </button>
    </>
  );
}

function TicketFacts({ ticket }: { ticket: MaintenanceTicket }) {
  return (
    <div className="facts">
      <div>
        <div className="muted small">Tool</div>
        <strong>{ticket.tool_id}</strong>
      </div>
      <div>
        <div className="muted small">TN</div>
        <strong>{ticket.tn ?? "—"}</strong>
      </div>
      <div>
        <div className="muted small">Reason</div>
        <strong>{ticket.finding}</strong>
      </div>
    </div>
  );
}

function NewTicketDialog({ machines, onClose, onDone }: { machines: LocationRow[]; onClose: () => void; onDone: (ticket: MaintenanceTicket) => void }) {
  const [toolId, setToolId] = useState("");
  const [origin, setOrigin] = useState<"machine" | "toolcrib">("machine");
  const [machine, setMachine] = useState("");
  const [partNumber, setPartNumber] = useState("");
  const [station, setStation] = useState("");
  const [values, setValues] = useState<IncidentValues>({ operator: "", cause: "", moment: "" });
  const [finding, setFinding] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fromMachine = origin === "machine";

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!toolCode(toolId)) return setError("Scan the tool or type its ID.");
    if (fromMachine && !machine) return setError("Choose the machine the tool came from.");
    const missing = incidentMissing(fromMachine ? values : { ...values, operator: "-" });
    if (missing) return setError(missing);
    if (finding.trim().length < 3) return setError("Write what Tool Crib found on the tool.");
    setBusy(true);
    setError(null);
    try {
      onDone(
        await api<MaintenanceTicket>("POST", "/workflow/maintenance/tickets", {
          tool_id: toolId,
          origen: origin,
          machine: fromMachine ? machine : null,
          part_number: fromMachine ? partNumber.trim() || null : null,
          station: fromMachine ? station.trim() || null : null,
          operador: fromMachine ? values.operator : null,
          causa: values.cause,
          momento: values.moment,
          hallazgo: finding.trim(),
        }),
      );
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The ticket could not be opened.");
      setBusy(false);
    }
  }

  return (
    <Dialog title="New maintenance ticket" onClose={onClose} footer={<Footer busy={busy} label="Open ticket" onClose={onClose} onSubmit={() => void submit()} />}>
      <form className="form" onSubmit={(event) => void submit(event)}>
        <p className="note note-info">Use this for a tool Tool Crib already holds. For a tool still on a machine, open the request in Requests and press Send to Maintenance.</p>
        <label>
          Tool
          <ScanInput className="big-input" value={toolId} onValue={(value) => setToolId(value)} placeholder="Scan the tool, or type its ID" autoFocus />
        </label>
        <div className="svc-options">
          <label className={`svc-option${fromMachine ? " selected" : ""}`}>
            <input type="radio" name="origin" checked={fromMachine} onChange={() => setOrigin("machine")} />
            <span>
              <strong>Came from a machine</strong>
              <span className="muted small">Record the machine, part number, station and who was on it.</span>
            </span>
          </label>
          <label className={`svc-option${!fromMachine ? " selected" : ""}`}>
            <input type="radio" name="origin" checked={!fromMachine} onChange={() => setOrigin("toolcrib")} />
            <span>
              <strong>Found in Tool Crib</strong>
              <span className="muted small">Damage noticed in a rack, at the TOGU or in a return.</span>
            </span>
          </label>
        </div>
        {fromMachine && (
          <>
            <label>
              Machine
              <select value={machine} onChange={(event) => setMachine(event.target.value)}>
                <option value="">Choose…</option>
                {machines.map((item) => (
                  <option key={item.codigo} value={item.codigo}>
                    {item.codigo}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Part number (optional)
              <input value={partNumber} onChange={(event) => setPartNumber(event.target.value)} maxLength={80} />
            </label>
            <label>
              Station (optional)
              <input value={station} onChange={(event) => setStation(event.target.value)} maxLength={20} />
            </label>
          </>
        )}
        <IncidentFields values={values} onChange={setValues} showOperator={fromMachine} />
        <label>
          What Tool Crib found (the reason for the ticket)
          <textarea rows={3} maxLength={500} value={finding} onChange={(event) => setFinding(event.target.value)} />
        </label>
        <ErrorNote message={error} />
        <button type="submit" hidden />
      </form>
    </Dialog>
  );
}

function ReceiveDialog({ ticket, onClose, onDone }: { ticket: MaintenanceTicket; onClose: () => void; onDone: (ticket: MaintenanceTicket) => void }) {
  const [scanned, setScanned] = useState("");
  const [capture, setCapture] = useState<Capture>("manual");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!sameCode(toolCode(scanned), ticket.tool_id)) return setError(`Scan the tool ${ticket.tool_id} to confirm you have it.`);
    setBusy(true);
    setError(null);
    try {
      onDone(await api<MaintenanceTicket>("POST", `/workflow/maintenance/tickets/${ticket.id}/receive`, { tool_id: scanned, captura: capture }));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The tool could not be received.");
      setBusy(false);
    }
  }

  return (
    <Dialog title={`Receive · ${ticket.folio}`} onClose={onClose} footer={<Footer busy={busy} label="Receive tool" onClose={onClose} onSubmit={() => void submit()} />}>
      <form className="form" onSubmit={(event) => void submit(event)}>
        <TicketFacts ticket={ticket} />
        <label>
          Scan the tool
          <ScanInput className="big-input" value={scanned} onValue={(value, mode) => { setScanned(value); setCapture(mode); }} placeholder="Scan the tool, or type its ID" autoFocus />
        </label>
        <ErrorNote message={error} />
        <button type="submit" hidden />
      </form>
    </Dialog>
  );
}

function EvaluateDialog({ ticket, onClose, onDone }: { ticket: MaintenanceTicket; onClose: () => void; onDone: (ticket: MaintenanceTicket) => void }) {
  const [diagnosis, setDiagnosis] = useState("");
  const [repairable, setRepairable] = useState<boolean | null>(null);
  const [cause, setCause] = useState(ticket.incident?.cause ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (diagnosis.trim().length < 3) return setError("Write what you found.");
    if (repairable === null) return setError("Say whether the tool can be repaired.");
    setBusy(true);
    setError(null);
    try {
      onDone(
        await api<MaintenanceTicket>("POST", `/workflow/maintenance/tickets/${ticket.id}/evaluate`, {
          diagnostico: diagnosis.trim(),
          reparable: repairable,
          causa_tecnica: cause || null,
        }),
      );
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The evaluation could not be saved.");
      setBusy(false);
    }
  }

  return (
    <Dialog title={`Evaluate · ${ticket.folio}`} onClose={onClose} footer={<Footer busy={busy} label="Save evaluation" danger={repairable === false} onClose={onClose} onSubmit={() => void submit()} />}>
      <div className="form">
        <TicketFacts ticket={ticket} />
        {ticket.incident && (
          <p className="muted small">
            Reported cause: {causeLabel(ticket.incident.cause)} · {momentLabel(ticket.incident.moment)} · {ticket.incident.operator ?? "no operator"}
          </p>
        )}
        <label>
          Diagnosis
          <textarea rows={3} maxLength={500} value={diagnosis} onChange={(event) => setDiagnosis(event.target.value)} autoFocus />
        </label>
        <label>
          Cause according to Maintenance
          <select value={cause} onChange={(event) => setCause(event.target.value)}>
            <option value="">Same as reported</option>
            {DAMAGE_CAUSES.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <div className="svc-options">
          <label className={`svc-option${repairable === true ? " selected" : ""}`}>
            <input type="radio" name="repairable" checked={repairable === true} onChange={() => setRepairable(true)} />
            <span>
              <strong>Can be repaired</strong>
              <span className="muted small">The ticket stays open until you release the tool.</span>
            </span>
          </label>
          <label className={`svc-option${repairable === false ? " selected" : ""}`}>
            <input type="radio" name="repairable" checked={repairable === false} onChange={() => setRepairable(false)} />
            <span>
              <strong>Cannot be repaired</strong>
              <span className="muted small">The ticket closes and the tool stays damaged in Maintenance until it is retired.</span>
            </span>
          </label>
        </div>
        <ErrorNote message={error} />
      </div>
    </Dialog>
  );
}

function ReleaseDialog({ ticket, onClose, onDone }: { ticket: MaintenanceTicket; onClose: () => void; onDone: (ticket: MaintenanceTicket) => void }) {
  const [work, setWork] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (work.trim().length < 3) return setError("Describe the work you did.");
    setBusy(true);
    setError(null);
    try {
      onDone(await api<MaintenanceTicket>("POST", `/workflow/maintenance/tickets/${ticket.id}/release`, { trabajo: work.trim() }));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The tool could not be released.");
      setBusy(false);
    }
  }

  return (
    <Dialog title={`Release · ${ticket.folio}`} onClose={onClose} footer={<Footer busy={busy} label="Release tool" onClose={onClose} onSubmit={() => void submit()} />}>
      <div className="form">
        <TicketFacts ticket={ticket} />
        {ticket.diagnosis && <p className="note note-info">Diagnosis: {ticket.diagnosis}</p>}
        <label>
          Work done
          <textarea rows={3} maxLength={500} value={work} onChange={(event) => setWork(event.target.value)} autoFocus />
        </label>
        <p className="muted small">The tool becomes available and stays in Maintenance until Tool Crib picks it up.</p>
        <ErrorNote message={error} />
      </div>
    </Dialog>
  );
}

export function MaintenancePage() {
  const { can } = useAuth();
  const canOperate = can("maintenance.operate");
  const canOpen = can("service.attend") && can("tools.move");
  const [view, setView] = useState("active");
  const [rows, setRows] = useState<MaintenanceTicket[] | null>(null);
  const [machines, setMachines] = useState<LocationRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [receiving, setReceiving] = useState<MaintenanceTicket | null>(null);
  const [evaluating, setEvaluating] = useState<MaintenanceTicket | null>(null);
  const [releasing, setReleasing] = useState<MaintenanceTicket | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await api<MaintenanceTicket[]>("GET", `/workflow/maintenance/tickets?state=${view}`));
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load the tickets.");
    }
  }, [view]);
  usePoll(load, 15000);

  useEffect(() => {
    setRows(null);
    void load();
  }, [load]);

  useEffect(() => {
    if (!canOpen) return;
    api<LocationRow[]>("GET", "/workflow/locations")
      .then((all) => setMachines(all.filter((item) => item.activa && item.tipo === "machine").sort((a, b) => a.codigo.localeCompare(b.codigo))))
      .catch(() => setMachines([]));
  }, [canOpen]);

  const counts = useMemo(() => {
    const all = rows ?? [];
    return {
      open: all.filter((row) => row.state === "open").length,
      evaluating: all.filter((row) => row.state === "evaluating").length,
      repairing: all.filter((row) => row.state === "repairing").length,
    };
  }, [rows]);

  function done(text: string) {
    setCreating(false);
    setReceiving(null);
    setEvaluating(null);
    setReleasing(null);
    setMessage(text);
    void load();
  }

  return (
    <section className="page">
      <div className="board-bar">
        <h1>Maintenance</h1>
        <div className="bar-right">
          <select value={view} onChange={(event) => setView(event.target.value)} aria-label="Show">
            {VIEWS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <button className="btn" onClick={() => void load()}>
            Refresh
          </button>
          {canOpen && (
            <button className="btn btn-primary" onClick={() => setCreating(true)}>
              New ticket
            </button>
          )}
        </div>
      </div>
      <p className="muted">Tickets are opened by Tool Crib. The technician receives the tool, evaluates it, repairs it and releases it; Tool Crib then picks it up from Maintenance.</p>
      <div className="summary">
        <span className="chip sum lvl-ROJO">{counts.open} waiting</span>
        <span className="chip sum lvl-AMARILLO">{counts.evaluating} being evaluated</span>
        <span className="chip sum lvl-VERDE">{counts.repairing} being repaired</span>
      </div>
      <ErrorNote message={error} />
      {message && <div className="note note-info">✔ {message}</div>}
      {rows && rows.length === 0 && !error && <p className="note note-info">{view === "active" ? "No open tickets." : "Nothing to show."}</p>}
      {rows && rows.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Ticket</th>
                <th>Tool</th>
                <th>What happened</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((ticket) => {
                const incident = ticket.incident;
                return (
                  <tr key={ticket.id} className={ticket.state === "closed" ? "inactive" : ""}>
                    <td>
                      <strong>{ticket.folio}</strong>
                      <div className="muted small">{ticket.request_folio ? `From ${ticket.request_folio}` : "Opened by hand"}</div>
                      <div className="muted small">
                        {ticket.created_by_name ?? ticket.created_by} · {when(ticket.created_at)}
                      </div>
                    </td>
                    <td>
                      <strong>{ticket.tool_id}</strong>
                      <div className="muted small">
                        TN {ticket.tn ?? "—"}
                        {ticket.category ? ` · ${ticket.category === "punzon" ? "Punch" : "Die"}` : ""}
                      </div>
                      <div className="muted small">
                        {ticket.tool_location ?? "—"} · {TOOL_STATE_LABEL[ticket.tool_status ?? ""] ?? ticket.tool_status ?? ""}
                      </div>
                    </td>
                    <td>
                      <div className="warn-text small">“{ticket.finding}”</div>
                      {incident && (
                        <div className="muted small">
                          {incident.machine ? `${incident.machine}${incident.part_number ? ` · ${incident.part_number}` : ""}${incident.station ? ` · station ${incident.station}` : ""}` : "Found in Tool Crib"}
                          <div>
                            {causeLabel(incident.cause)} · {momentLabel(incident.moment)}
                            {incident.operator ? ` · ${incident.operator}` : ""}
                            {incident.shift ? ` (${incident.shift})` : ""}
                          </div>
                          {incident.reported_as && <div>Reported as {incident.reported_as}</div>}
                        </div>
                      )}
                      {ticket.diagnosis && <div className="muted small">Diagnosis: {ticket.diagnosis}</div>}
                      {ticket.work && <div className="muted small">Work: {ticket.work}</div>}
                    </td>
                    <td>
                      <Chip
                        text={ticket.result === "unrepairable" ? "Cannot be repaired" : ticket.result === "released" ? "Released" : (TICKET_STATE_LABEL[ticket.state] ?? ticket.state)}
                        className={TICKET_STATE_CLASS[ticket.state] ?? ""}
                      />
                      <div className="muted small">
                        {ticket.received_by && `${ticket.received_by_name ?? ticket.received_by}`}
                        {ticket.closed_at && ` · ${when(ticket.closed_at)}`}
                      </div>
                    </td>
                    <td className="row-actions">
                      {canOperate && ticket.state === "open" && (
                        <button className="btn btn-primary" onClick={() => setReceiving(ticket)}>
                          Receive…
                        </button>
                      )}
                      {canOperate && ticket.state === "evaluating" && (
                        <button className="btn btn-primary" onClick={() => setEvaluating(ticket)}>
                          Evaluate…
                        </button>
                      )}
                      {canOperate && ticket.state === "repairing" && (
                        <button className="btn btn-primary" onClick={() => setReleasing(ticket)}>
                          Release…
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
      {!rows && !error && <p className="muted">Loading tickets…</p>}

      {creating && <NewTicketDialog machines={machines} onClose={() => setCreating(false)} onDone={(ticket) => done(`${ticket.folio} opened for ${ticket.tool_id}. Take it to ${ticket.tool_location}.`)} />}
      {receiving && <ReceiveDialog ticket={receiving} onClose={() => setReceiving(null)} onDone={(ticket) => done(`${ticket.folio} received. Evaluate the tool.`)} />}
      {evaluating && (
        <EvaluateDialog
          ticket={evaluating}
          onClose={() => setEvaluating(null)}
          onDone={(ticket) => done(ticket.result === "unrepairable" ? `${ticket.tool_id} cannot be repaired; it stays damaged in Maintenance.` : `${ticket.folio} is being repaired.`)}
        />
      )}
      {releasing && <ReleaseDialog ticket={releasing} onClose={() => setReleasing(null)} onDone={(ticket) => done(`${ticket.tool_id} released. Tool Crib can pick it up from ${ticket.tool_location}.`)} />}
    </section>
  );
}
