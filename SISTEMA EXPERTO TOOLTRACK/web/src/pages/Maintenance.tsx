import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { Dialog, ErrorNote } from "../components/Dialog";
import { Chip } from "../components/MachineCard";
import { MachineLink, ToolLink } from "../components/Links";
import { ScanInput } from "../components/ScanInput";
import { causeLabel, IncidentFields, incidentMissing, momentLabel, when, type IncidentValues } from "../components/Service";
import { DAMAGE_CAUSES, RETIREMENT_STATE_CLASS, RETIREMENT_STATE_LABEL, TICKET_STATE_CLASS, TICKET_STATE_LABEL, TOOL_STATE_LABEL } from "../labels";
import { sameCode, toolCode, type Capture } from "../scan";
import type { LocationRow, MaintenanceTicket, Retirement } from "../types";
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

export function RetireToolDialog({ toolId, onClose, onDone }: { toolId: string; onClose: () => void; onDone: () => void }) {
  const [machines, setMachines] = useState<LocationRow[]>([]);
  useEffect(() => {
    api<LocationRow[]>("GET", "/workflow/locations")
      .then((all) => setMachines(all.filter((item) => item.activa && item.tipo === "machine").sort((a, b) => a.codigo.localeCompare(b.codigo))))
      .catch(() => setMachines([]));
  }, []);
  return <NewTicketDialog machines={machines} retirement initialTool={toolId} onClose={onClose} onDone={onDone} />;
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

export function NewTicketDialog({ machines, retirement = false, initialTool = "", onClose, onDone }: { machines: LocationRow[]; retirement?: boolean; initialTool?: string; onClose: () => void; onDone: (ticket: MaintenanceTicket) => void }) {
  const [toolId, setToolId] = useState(initialTool);
  const [origin, setOrigin] = useState<"machine" | "toolcrib">(initialTool ? "toolcrib" : "machine");
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
    if (finding.trim().length < 3) return setError(retirement ? "Write why the tool should be retired." : "Write what Tool Crib found on the tool.");
    setBusy(true);
    setError(null);
    try {
      onDone(
        await api<MaintenanceTicket>("POST", retirement ? "/workflow/retirements" : "/workflow/maintenance/tickets", {
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
      setError(caught instanceof ApiError ? caught.message : retirement ? "The retirement could not be proposed." : "The ticket could not be opened.");
      setBusy(false);
    }
  }

  return (
    <Dialog title={retirement ? "Retire a tool" : "New maintenance ticket"} onClose={onClose} footer={<Footer busy={busy} label={retirement ? "Propose retirement" : "Open ticket"} danger={retirement} onClose={onClose} onSubmit={() => void submit()} />}>
      <form className="form" onSubmit={(event) => void submit(event)}>
        <label>
          Tool
          <ScanInput className="big-input" value={toolId} onValue={(value) => setToolId(value)} placeholder="Scan the tool, or type its ID" autoFocus />
        </label>
        <div className="svc-options">
          <label className={`svc-option${fromMachine ? " selected" : ""}`}>
            <input type="radio" name="origin" checked={fromMachine} onChange={() => setOrigin("machine")} />
            <span>
              <strong>Came from a machine</strong>
            </span>
          </label>
          <label className={`svc-option${!fromMachine ? " selected" : ""}`}>
            <input type="radio" name="origin" checked={!fromMachine} onChange={() => setOrigin("toolcrib")} />
            <span>
              <strong>Found in Tool Crib</strong>
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
          {retirement ? "Why the tool should be retired" : "What Tool Crib found (the reason for the ticket)"}
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
            </span>
          </label>
          <label className={`svc-option${repairable === false ? " selected" : ""}`}>
            <input type="radio" name="repairable" checked={repairable === false} onChange={() => setRepairable(false)} />
            <span>
              <strong>Cannot be repaired</strong>
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
        <ErrorNote message={error} />
      </div>
    </Dialog>
  );
}

function TicketsPanel() {
  const { can } = useAuth();
  const canOperate = can("maintenance.operate");
  const canOpen = can("service.attend") && can("tools.move");
  const canRetire = canOpen;
  const [view, setView] = useState("active");
  const [retiring, setRetiring] = useState<MaintenanceTicket | null>(null);
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
    setRetiring(null);
    setMessage(text);
    void load();
  }

  return (
    <div>
      <div className="board-bar">
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
                      <strong>
                        <ToolLink id={ticket.tool_id} />
                      </strong>
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
                          {incident.machine ? (
                            <>
                              <MachineLink code={incident.machine} />
                              {`${incident.part_number ? ` · ${incident.part_number}` : ""}${incident.station ? ` · station ${incident.station}` : ""}`}
                            </>
                          ) : (
                            "Found in Tool Crib"
                          )}
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
                      {ticket.retirement && (
                        <div className="muted small">
                          Retirement {ticket.retirement.folio} · {RETIREMENT_STATE_LABEL[ticket.retirement.state] ?? ticket.retirement.state}
                        </div>
                      )}
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
                      {canRetire && ticket.result === "unrepairable" && ticket.tool_status === "danado" && (!ticket.retirement || ticket.retirement.state === "rejected") && (
                        <button className="btn btn-danger" onClick={() => setRetiring(ticket)}>
                          Retire…
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
      {retiring && <RetireTicketDialog ticket={retiring} onClose={() => setRetiring(null)} onDone={() => done(`Retirement of ${retiring.tool_id} proposed. A supervisor or the technician has to approve it.`)} />}
    </div>
  );
}

function RetireTicketDialog({ ticket, onClose, onDone }: { ticket: MaintenanceTicket; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (reason.trim().length < 3) return setError("Write why the tool should be retired.");
    setBusy(true);
    setError(null);
    try {
      await api("POST", "/workflow/retirements", { ticket_id: ticket.id, hallazgo: reason.trim() });
      onDone();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The retirement could not be proposed.");
      setBusy(false);
    }
  }

  return (
    <Dialog title={`Retire · ${ticket.folio}`} onClose={onClose} footer={<Footer busy={busy} label="Propose retirement" danger onClose={onClose} onSubmit={() => void submit()} />}>
      <div className="form">
        <TicketFacts ticket={ticket} />
        {ticket.diagnosis && <p className="note note-info">Maintenance diagnosis: {ticket.diagnosis}</p>}
        <label>
          Why the tool should be retired (required)
          <textarea rows={3} maxLength={500} value={reason} onChange={(event) => setReason(event.target.value)} autoFocus />
        </label>
        <ErrorNote message={error} />
      </div>
    </Dialog>
  );
}

function RetirementDecisionDialog({ retirement, approve, onClose, onDone }: { retirement: Retirement; approve: boolean; onClose: () => void; onDone: (retirement: Retirement) => void }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!approve && note.trim().length < 3) return setError("Say why the retirement is rejected.");
    setBusy(true);
    setError(null);
    try {
      onDone(await api<Retirement>("POST", `/workflow/retirements/${retirement.id}/${approve ? "approve" : "reject"}`, { nota: note.trim() || null }));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The decision could not be saved.");
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={`${approve ? "Approve" : "Reject"} · ${retirement.folio}`}
      onClose={onClose}
      footer={<Footer busy={busy} label={approve ? "Approve retirement" : "Reject retirement"} danger={approve} onClose={onClose} onSubmit={() => void submit()} />}
    >
      <div className="form">
        <div className="facts">
          <div>
            <div className="muted small">Tool</div>
            <strong>{retirement.tool_id}</strong>
          </div>
          <div>
            <div className="muted small">TN</div>
            <strong>{retirement.tn ?? "—"}</strong>
          </div>
          <div>
            <div className="muted small">Reason</div>
            <strong>{retirement.reason}</strong>
          </div>
        </div>
        <p className="note note-warn">{approve ? "The tool is retired for good." : "The tool goes back to the state it had."}</p>
        <label>
          {approve ? "Note (optional)" : "Why is it rejected? (required)"}
          <textarea rows={2} maxLength={500} value={note} onChange={(event) => setNote(event.target.value)} autoFocus />
        </label>
        <ErrorNote message={error} />
      </div>
    </Dialog>
  );
}

function RetirementsPanel() {
  const { session, can } = useAuth();
  const canDecide = can("retirements.approve");
  const canPropose = can("service.attend") && can("tools.move");
  const [view, setView] = useState("pending");
  const [rows, setRows] = useState<Retirement[] | null>(null);
  const [machines, setMachines] = useState<LocationRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [deciding, setDeciding] = useState<{ retirement: Retirement; approve: boolean } | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await api<Retirement[]>("GET", `/workflow/retirements?state=${view}`));
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load the retirements.");
    }
  }, [view]);
  usePoll(load, 15000);

  useEffect(() => {
    setRows(null);
    void load();
  }, [load]);

  useEffect(() => {
    if (!canPropose) return;
    api<LocationRow[]>("GET", "/workflow/locations")
      .then((all) => setMachines(all.filter((item) => item.activa && item.tipo === "machine").sort((a, b) => a.codigo.localeCompare(b.codigo))))
      .catch(() => setMachines([]));
  }, [canPropose]);

  function done(text: string) {
    setCreating(false);
    setDeciding(null);
    setMessage(text);
    void load();
  }

  return (
    <div>
      <div className="board-bar">
        <div className="bar-right">
          <select value={view} onChange={(event) => setView(event.target.value)} aria-label="Show">
            <option value="pending">Waiting for approval</option>
            <option value="closed">Decided</option>
            <option value="all">All</option>
          </select>
          <button className="btn" onClick={() => void load()}>
            Refresh
          </button>
          {canPropose && (
            <button className="btn btn-primary" onClick={() => setCreating(true)}>
              Retire a tool
            </button>
          )}
        </div>
      </div>
      <ErrorNote message={error} />
      {message && <div className="note note-info">✔ {message}</div>}
      {rows && rows.length === 0 && !error && <p className="note note-info">{view === "pending" ? "No retirements waiting." : "Nothing to show."}</p>}
      {rows && rows.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Retirement</th>
                <th>Tool</th>
                <th>What happened</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const incident = row.incident;
                const own = row.proposed_by === session?.username;
                return (
                  <tr key={row.id} className={row.state === "pending" ? "" : "inactive"}>
                    <td>
                      <strong>{row.folio}</strong>
                      <div className="muted small">{row.request_folio ? `From ${row.request_folio}` : "Proposed by hand"}</div>
                      <div className="muted small">
                        {row.proposed_by_name ?? row.proposed_by} · {when(row.proposed_at)}
                      </div>
                    </td>
                    <td>
                      <strong>
                        <ToolLink id={row.tool_id} />
                      </strong>
                      <div className="muted small">
                        TN {row.tn ?? "—"}
                        {row.category ? ` · ${row.category === "punzon" ? "Punch" : "Die"}` : ""}
                      </div>
                      <div className="muted small">
                        {row.tool_location ?? "—"} · {TOOL_STATE_LABEL[row.tool_status ?? ""] ?? row.tool_status ?? ""}
                      </div>
                    </td>
                    <td>
                      <div className="warn-text small">“{row.reason}”</div>
                      {incident && (
                        <div className="muted small">
                          {incident.machine ? (
                            <>
                              <MachineLink code={incident.machine} />
                              {`${incident.part_number ? ` · ${incident.part_number}` : ""}${incident.station ? ` · station ${incident.station}` : ""}`}
                            </>
                          ) : (
                            "Found in Tool Crib"
                          )}
                          <div>
                            {causeLabel(incident.cause)} · {momentLabel(incident.moment)}
                            {incident.operator ? ` · ${incident.operator}` : ""}
                            {incident.shift ? ` (${incident.shift})` : ""}
                          </div>
                          {incident.reported_as && <div>Reported as {incident.reported_as}</div>}
                        </div>
                      )}
                    </td>
                    <td>
                      <Chip text={RETIREMENT_STATE_LABEL[row.state] ?? row.state} className={RETIREMENT_STATE_CLASS[row.state] ?? ""} />
                      <div className="muted small">
                        {row.resolved_by && `${row.resolved_by_name ?? row.resolved_by} · ${when(row.resolved_at)}`}
                        {row.resolution_note && <div>{row.resolution_note}</div>}
                      </div>
                    </td>
                    <td className="row-actions">
                      {canDecide && row.state === "pending" && !own && (
                        <>
                          <button className="btn btn-danger" onClick={() => setDeciding({ retirement: row, approve: true })}>
                            Approve…
                          </button>
                          <button className="btn" onClick={() => setDeciding({ retirement: row, approve: false })}>
                            Reject…
                          </button>
                        </>
                      )}
                      {canDecide && row.state === "pending" && own && <span className="muted small">Someone else decides</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {!rows && !error && <p className="muted">Loading retirements…</p>}

      {creating && <NewTicketDialog machines={machines} retirement onClose={() => setCreating(false)} onDone={() => done("Retirement proposed. A supervisor or the technician has to approve it.")} />}
      {deciding && (
        <RetirementDecisionDialog
          retirement={deciding.retirement}
          approve={deciding.approve}
          onClose={() => setDeciding(null)}
          onDone={(retirement) => done(retirement.state === "approved" ? `${retirement.tool_id} retired.` : `${retirement.tool_id} is back in use.`)}
        />
      )}
    </div>
  );
}

export function MaintenancePage() {
  const [tab, setTab] = useState<"tickets" | "retirements">("tickets");
  return (
    <section className="page">
      <div className="board-bar">
        <h1>Maintenance</h1>
      </div>
      <div className="tabs-inline wrap">
        <button className={`btn${tab === "tickets" ? " btn-primary" : ""}`} onClick={() => setTab("tickets")}>
          Tickets
        </button>
        <button className={`btn${tab === "retirements" ? " btn-primary" : ""}`} onClick={() => setTab("retirements")}>
          Retirements
        </button>
      </div>
      {tab === "tickets" ? <TicketsPanel /> : <RetirementsPanel />}
    </section>
  );
}
