import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { Dialog, ErrorNote } from "../components/Dialog";
import { ReasonDialog } from "../components/ReasonDialog";
import { LocationPicker } from "../components/LocationPicker";
import { ScanInput } from "../components/ScanInput";
import { sameCode, toolCode, type Capture } from "../scan";
import { ABNORMAL_REBAJE, IncidentFields, IncidentSummary, incidentMissing, isActive, isWaitingForTool, needsServiceResult, positionLabel, problemLabel, RequestChip, WAIT_LATE_MIN, WAIT_WARN_MIN, waitClass, waitMinutes, waitText, when, type IncidentValues } from "../components/Service";
import { SERVICE_TYPE_LABEL, TOOL_STATE_LABEL } from "../labels";
import type { LocationRow, ServiceRequest } from "../types";
import { usePoll } from "../usePoll";

const DESTINATION_TYPES = ["rack", "togu", "maintenance", "workshop"];
const SUPERVISOR_ROLES = ["TOOLCRIB_SUPERVISOR", "SYSTEM_ADMIN"];
const VIEWS = [
  { value: "active", label: "Open" },
  { value: "closed", label: "Closed" },
  { value: "all", label: "All" },
];
const SERVICE_PLACES = ["togu", "maintenance", "workshop"];
const COLLECT_HINT: Record<string, string> = {
  sharpening: "The tool will be marked as being sharpened.",
  damage: "The tool will be marked as damaged.",
  return: "The request closes once the tool is recorded; the position stays empty.",
};

function CollectDialog({ request, destinations, relocate, onClose, onDone }: { request: ServiceRequest; destinations: LocationRow[]; relocate?: boolean; onClose: () => void; onDone: (request: ServiceRequest) => void }) {
  const [destination, setDestination] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Work on a tool happens in a service location; only a return goes straight to a rack.
  const allowed = request.type === "return" ? destinations : destinations.filter((place) => SERVICE_PLACES.includes(place.tipo));

  async function submit() {
    if (!destination) return setError("Choose where the tool is going.");
    setBusy(true);
    setError(null);
    try {
      onDone(await api<ServiceRequest>("POST", `/workflow/service-requests/${request.id}/${relocate ? "relocate" : "collect"}`, { destino_codigo: destination, nota: note.trim() || null }));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The pickup could not be recorded.");
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={relocate ? `Move the tool · ${request.folio}` : `Tool collected · ${request.folio}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Back
          </button>
          <button className="btn btn-primary" onClick={() => void submit()} disabled={busy}>
            {busy ? "Saving…" : relocate ? "Move tool" : "Save pickup"}
          </button>
        </>
      }
    >
      <div className="form">
        <div className="facts">
          <div>
            <div className="muted small">Tool</div>
            <strong>{request.tool_id}</strong>
          </div>
          <div>
            <div className="muted small">{relocate ? "Now in" : "Taken from"}</div>
            <strong>{relocate ? request.tool_location ?? "—" : request.machine}</strong>
          </div>
          <div>
            <div className="muted small">Request</div>
            <strong>{SERVICE_TYPE_LABEL[request.type] ?? request.type}</strong>
          </div>
        </div>
        {request.note && <p className="note note-warn">Operator note: {request.note}</p>}
        {relocate && <p className="note note-warn">The tool is not in a service location, so its result cannot be recorded. Move it to the place where the work is done.</p>}
        <LocationPicker label={request.type === "return" ? "Where does the tool go?" : "Which service location?"} options={allowed} value={destination} onChange={setDestination} autoFocus showType />
        {!relocate && COLLECT_HINT[request.type] && <p className="muted small">{COLLECT_HINT[request.type]}</p>}
        <label>
          Notes (optional)
          <input value={note} onChange={(event) => setNote(event.target.value)} maxLength={200} />
        </label>
        <ErrorNote message={error} />
      </div>
    </Dialog>
  );
}

function FinishDialog({ request, onClose, onDone }: { request: ServiceRequest; onClose: () => void; onDone: (request: ServiceRequest) => void }) {
  const [result, setResult] = useState<"ready" | "retired">("ready");
  const [rebaje, setRebaje] = useState("");
  const [justification, setJustification] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sharpening = request.type === "sharpening";
  const needsNote = result === "retired" || request.type === "damage" || request.type === "performance";
  const amount = rebaje.trim() === "" ? null : Number(rebaje);
  const abnormal = amount !== null && Number.isFinite(amount) && amount > ABNORMAL_REBAJE;

  async function submit() {
    if (result === "ready" && sharpening && amount === null) return setError("Record how much was ground off (0 if nothing was removed).");
    if (amount !== null && (!Number.isFinite(amount) || amount < 0)) return setError("The amount ground off must be a number, 0 or more.");
    if (abnormal && justification.trim().length < 10) return setError(`More than ${ABNORMAL_REBAJE.toFixed(3)} in. is abnormal: explain what happened.`);
    if (needsNote && !note.trim()) return setError(result === "retired" ? "Explain why the tool is retired." : "Describe what was found and repaired.");
    setBusy(true);
    setError(null);
    try {
      onDone(
        await api<ServiceRequest>("POST", `/workflow/service-requests/${request.id}/finish-service`, {
          resultado: result,
          rebaje: amount,
          justificacion: justification.trim() || null,
          nota: note.trim() || null,
        }),
      );
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The result could not be saved.");
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={`Service result · ${request.folio}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Back
          </button>
          <button className={`btn ${result === "retired" ? "btn-danger" : "btn-primary"}`} onClick={() => void submit()} disabled={busy}>
            {busy ? "Saving…" : result === "retired" ? "Retire the tool" : "Save result"}
          </button>
        </>
      }
    >
      <div className="form">
        <div className="facts">
          <div>
            <div className="muted small">Tool</div>
            <strong>{request.tool_id}</strong>
          </div>
          <div>
            <div className="muted small">Now in</div>
            <strong>{request.tool_location ?? "—"}</strong>
          </div>
          <div>
            <div className="muted small">Came from</div>
            <strong>
              {request.machine} · {request.part_number ?? "—"}
            </strong>
          </div>
        </div>
        {request.note && <p className="note note-warn">Operator note: {request.note}</p>}
        <label className="check">
          <input type="radio" name="finish-result" checked={result === "ready"} onChange={() => setResult("ready")} /> Ready for use
        </label>
        <label className="check">
          <input type="radio" name="finish-result" checked={result === "retired"} onChange={() => setResult("retired")} /> Retire the tool (it does not come back)
        </label>
        {result === "ready" && (
          <label>
            Ground off (in.){sharpening ? "" : " (optional)"}
            <input inputMode="decimal" value={rebaje} onChange={(event) => setRebaje(event.target.value)} placeholder="e.g. 0.015" autoFocus />
          </label>
        )}
        {abnormal && (
          <>
            <p className="note note-warn">More than {ABNORMAL_REBAJE.toFixed(3)} in. was ground off. That is abnormal, so it needs a good justification.</p>
            <label>
              Justification (required)
              <textarea rows={2} maxLength={500} value={justification} onChange={(event) => setJustification(event.target.value)} />
            </label>
          </>
        )}
        <label>
          {result === "retired" ? "Why is it retired? (required)" : needsNote ? "What was found and repaired? (required)" : "Notes (optional)"}
          <textarea rows={2} maxLength={500} value={note} onChange={(event) => setNote(event.target.value)} />
        </label>
        <ErrorNote message={error} />
      </div>
    </Dialog>
  );
}

function StoreDialog({ request, racks, onClose, onDone }: { request: ServiceRequest; racks: LocationRow[]; onClose: () => void; onDone: (request: ServiceRequest) => void }) {
  const [rack, setRack] = useState("");
  const [scanned, setScanned] = useState("");
  const [capture, setCapture] = useState<Capture>("manual");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!rack) return setError("Choose the rack, or scan its label.");
    if (!sameCode(toolCode(scanned), request.tool_id ?? "")) return setError(`Scan the tool ${request.tool_id} to confirm it is the one going to the rack.`);
    setBusy(true);
    setError(null);
    try {
      onDone(await api<ServiceRequest>("POST", `/workflow/service-requests/${request.id}/store`, { rack_code: rack, tool_id: scanned, captura: capture }));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The tool could not be stored.");
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={`Store in rack · ${request.folio}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Back
          </button>
          <button className="btn btn-primary" onClick={() => void submit()} disabled={busy}>
            {busy ? "Saving…" : "Store and close"}
          </button>
        </>
      }
    >
      <form className="form" onSubmit={(event) => void submit(event)}>
        <p className="note note-info">
          The machine already has its replacement. Store {request.tool_id} in its rack to close the request. It is only accepted in its own rack.
        </p>
        <LocationPicker label="Rack" options={racks} value={rack} onChange={setRack} autoFocus />
        <label>
          Scan the tool
          <ScanInput className="big-input" value={scanned} onValue={(value, mode) => { setScanned(value); setCapture(mode); }} placeholder="Scan the tool, or type its ID" />
        </label>
        <ErrorNote message={error} />
        {/* Two text fields and the buttons outside the form: without this, Enter after the scan would not submit. */}
        <button type="submit" hidden />
      </form>
    </Dialog>
  );
}

function IncidentDialog({ request, onClose, onDone }: { request: ServiceRequest; onClose: () => void; onDone: (request: ServiceRequest) => void }) {
  const existing = request.incident;
  const [values, setValues] = useState<IncidentValues>({ operator: existing?.operator ?? "", cause: existing?.cause ?? "", moment: existing?.moment ?? "" });
  const [finding, setFinding] = useState(existing?.finding ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    const missing = incidentMissing(values);
    if (missing) return setError(missing);
    if (finding.trim().length < 3) return setError("Write what Tool Crib found on the tool.");
    setBusy(true);
    setError(null);
    try {
      onDone(
        await api<ServiceRequest>("POST", `/workflow/service-requests/${request.id}/incident`, {
          operador: values.operator,
          causa: values.cause,
          momento: values.moment,
          hallazgo: finding.trim(),
        }),
      );
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The incident could not be saved.");
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={`Incident · ${request.folio}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Back
          </button>
          <button className="btn btn-primary" onClick={() => void submit()} disabled={busy}>
            {busy ? "Saving…" : "Save incident"}
          </button>
        </>
      }
    >
      <div className="form">
        <div className="facts">
          <div>
            <div className="muted small">Tool</div>
            <strong>{request.tool_id}</strong>
          </div>
          <div>
            <div className="muted small">Machine · part</div>
            <strong>
              {request.machine} · {request.part_number ?? "—"}
            </strong>
          </div>
          <div>
            <div className="muted small">Reported as</div>
            <strong>{SERVICE_TYPE_LABEL[request.type] ?? request.type}</strong>
          </div>
        </div>
        {request.note && <p className="note note-warn">Manufacturing said: {request.note}</p>}
        <IncidentFields values={values} onChange={setValues} />
        <label>
          What Tool Crib found
          <textarea rows={3} maxLength={500} value={finding} onChange={(event) => setFinding(event.target.value)} autoFocus />
        </label>
        <ErrorNote message={error} />
      </div>
    </Dialog>
  );
}

function ResolveDialog({ request, onClose, onDone }: { request: ServiceRequest; onClose: () => void; onDone: (request: ServiceRequest) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (text.trim().length < 3) return setError("Write what was done to resolve it.");
    setBusy(true);
    setError(null);
    try {
      onDone(await api<ServiceRequest>("POST", `/workflow/service-requests/${request.id}/resolve`, { resolucion: text.trim() }));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not close the request.");
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={`Resolve ${request.folio}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Back
          </button>
          <button className="btn btn-primary" onClick={() => void submit()} disabled={busy}>
            {busy ? "Saving…" : "Close request"}
          </button>
        </>
      }
    >
      <div className="form">
        <div className="facts">
          <div>
            <div className="muted small">Machine</div>
            <strong>{request.machine}</strong>
          </div>
          <div>
            <div className="muted small">Problem</div>
            <strong>{problemLabel(request.problem)}</strong>
          </div>
        </div>
        {request.note && <p className="note note-warn">Operator note: {request.note}</p>}
        <label>
          What was done?
          <textarea rows={3} maxLength={500} value={text} onChange={(event) => setText(event.target.value)} autoFocus />
        </label>
        <ErrorNote message={error} />
      </div>
    </Dialog>
  );
}

export function ServiceRequestsPage() {
  const { session, can } = useAuth();
  const canAttend = can("service.attend");
  const canHandleTools = can("tools.move");
  const [view, setView] = useState("active");
  const [rows, setRows] = useState<ServiceRequest[] | null>(null);
  const [destinations, setDestinations] = useState<LocationRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [collecting, setCollecting] = useState<ServiceRequest | null>(null);
  const [resolving, setResolving] = useState<ServiceRequest | null>(null);
  const [finishing, setFinishing] = useState<ServiceRequest | null>(null);
  const [storing, setStoring] = useState<ServiceRequest | null>(null);
  const [recording, setRecording] = useState<ServiceRequest | null>(null);
  const [relocating, setRelocating] = useState<ServiceRequest | null>(null);
  const [cancelling, setCancelling] = useState<ServiceRequest | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await api<ServiceRequest[]>("GET", `/workflow/service-requests?state=${view}`));
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load the requests.");
    }
  }, [view]);
  usePoll(load, 15000);

  useEffect(() => {
    setRows(null);
    void load();
  }, [load]);

  useEffect(() => {
    api<LocationRow[]>("GET", "/workflow/locations")
      .then((all) => setDestinations(all.filter((l) => l.activa && DESTINATION_TYPES.includes(l.tipo)).sort((a, b) => a.codigo.localeCompare(b.codigo))))
      .catch(() => setDestinations([]));
  }, []);

  const counts = useMemo(() => {
    const all = rows ?? [];
    return {
      open: all.filter((row) => row.state === "open").length,
      attending: all.filter((row) => row.state === "attending").length,
      collected: all.filter((row) => row.state === "collected").length,
      replaced: all.filter((row) => row.state === "replaced").length,
    };
  }, [rows]);

  async function attend(request: ServiceRequest) {
    setBusyId(request.id);
    setError(null);
    setMessage(null);
    try {
      await api<ServiceRequest>("POST", `/workflow/service-requests/${request.id}/attend`);
      setMessage(`You are attending ${request.folio}. Go to ${request.machine} for ${request.tool_id ?? `a TN ${request.tn} tool`}.`);
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not take the request.");
      await load();
    } finally {
      setBusyId(null);
    }
  }

  async function cancel(request: ServiceRequest, reason: string) {
    setCancelling(null);
    setError(null);
    setMessage(null);
    try {
      await api<ServiceRequest>("POST", `/workflow/service-requests/${request.id}/cancel`, { motivo: reason });
      setMessage(`${request.folio} cancelled.`);
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not cancel the request.");
    }
  }

  // Until the locations load, assume the tool is where it should be.
  function inServicePlace(request: ServiceRequest): boolean {
    if (destinations.length === 0) return true;
    const place = destinations.find((item) => item.codigo === request.tool_location);
    return !!place && SERVICE_PLACES.includes(place.tipo);
  }

  function collected(request: ServiceRequest) {
    setCollecting(null);
    setMessage(
      request.state === "closed"
        ? `${request.tool_id} recorded in ${request.destination}. ${request.folio} is closed.`
        : `${request.tool_id} recorded in ${request.destination}. Bring a tool with TN ${request.tn} to ${request.machine}; Manufacturing scans it to close ${request.folio}.`,
    );
    void load();
  }

  return (
    <section className="page">
      <div className="board-bar">
        <h1>Service requests</h1>
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
        </div>
      </div>
      <p className="muted">Requests from Manufacturing. Tool Crib presses Attending and goes to the machine; Engineering takes program problems. Rows turn amber after {WAIT_WARN_MIN} min and red after {WAIT_LATE_MIN} min without being closed.</p>
      <div className="summary">
        <span className="chip sum lvl-ROJO">{counts.open} waiting</span>
        <span className="chip sum lvl-AMARILLO">{counts.attending} attending</span>
        <span className="chip sum lvl-PARO">{counts.collected} machines waiting for a tool</span>
        <span className="chip sum lvl-VERDE">{counts.replaced} old tools in service</span>
      </div>
      <ErrorNote message={error} />
      {message && <div className="note note-info">✔ {message}</div>}
      {rows && rows.length === 0 && !error && <p className="note note-info">{view === "active" ? "No open requests." : "Nothing to show."}</p>}
      {rows && rows.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Request</th>
                <th>Machine · part</th>
                <th>Position · tool</th>
                <th className="hide-sm">Requested by</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((request) => {
                const done = !isActive(request);
                const minutes = waitMinutes(request);
                const waitingForTool = isWaitingForTool(request);
                const isProgram = request.type === "program_problem";
                const mine = request.attended_by === session?.username || SUPERVISOR_ROLES.includes(session?.role ?? "");
                const canTake = canAttend && (isProgram || canHandleTools);
                const toolReady = request.service?.result === "ready" && request.tool_status === "disponible";
                return (
                  <tr key={request.id} className={done ? "inactive" : waitingForTool ? waitClass(minutes) : ""}>
                    <td>
                      <strong>{request.folio}</strong>
                      <div className="muted small">{SERVICE_TYPE_LABEL[request.type] ?? request.type}</div>
                    </td>
                    <td>
                      <strong>{request.machine}</strong>
                      <div className="muted small">{request.part_number ?? "—"}</div>
                    </td>
                    <td>
                      {positionLabel(request)}
                      <div className="muted small">
                        {isProgram
                          ? problemLabel(request.problem)
                          : `${request.tool_id ?? "No tool: bring one"}${request.replacement_tool_id ? ` → ${request.replacement_tool_id}` : ""}`}
                      </div>
                      {request.note && <div className="warn-text small">“{request.note}”</div>}
                      {request.incident && <IncidentSummary incident={request.incident} />}
                    </td>
                    <td className="hide-sm">
                      {request.requested_by_name}
                      <div className="muted small">
                        {[request.requested_by_shift, when(request.requested_at)].filter(Boolean).join(" · ")}
                      </div>
                    </td>
                    <td>
                      <RequestChip request={request} />
                      <div className="muted small">
                        {waitingForTool && <strong className="wait-text">Waiting {waitText(minutes)}</strong>}
                        {request.state === "attending" && ` · ${request.attended_by_name}`}
                        {(request.state === "collected" || request.state === "replaced") && request.tool_id && (
                          <div>
                            {request.service
                              ? request.service.result === "retired"
                                ? `${request.tool_id} retired`
                                : `${request.tool_id} ready in ${request.tool_location ?? "—"}${request.service.rebaje !== null ? ` · ${request.service.rebaje.toFixed(3)} in. ground off` : ""}`
                              : `${request.tool_id} in ${request.tool_location ?? "—"} · ${TOOL_STATE_LABEL[request.tool_status ?? ""] ?? request.tool_status ?? ""}`}
                            {request.service?.abnormal && <span className="warn-text"> · abnormal</span>}
                          </div>
                        )}
                        {request.replacement_tool_id && request.state === "replaced" && <div>On the machine: {request.replacement_tool_id}</div>}
                        {request.state === "closed" && (request.resolution ? `${request.resolution} · ${when(request.closed_at)}` : when(request.closed_at))}
                        {request.state === "cancelled" && (request.cancel_reason ?? "")}
                      </div>
                    </td>
                    <td className="row-actions">
                      {canTake && request.state === "open" && (
                        <button className="btn btn-primary" disabled={busyId === request.id} onClick={() => void attend(request)}>
                          Attending
                        </button>
                      )}
                      {canTake && request.state === "attending" && !isProgram && request.type !== "tool_request" && mine && (
                        <button className="btn btn-primary" onClick={() => setCollecting(request)}>
                          Tool collected…
                        </button>
                      )}
                      {canHandleTools && (request.state === "collected" || request.state === "replaced") && !isProgram && request.type !== "tool_request" && (
                        <button className={`btn ${request.incident ? "" : "btn-primary"}`} onClick={() => setRecording(request)}>
                          {request.incident ? "Edit incident…" : "Record incident…"}
                        </button>
                      )}
                      {canHandleTools && needsServiceResult(request) && inServicePlace(request) && (
                        <button className="btn btn-primary" onClick={() => setFinishing(request)}>
                          Record result…
                        </button>
                      )}
                      {canHandleTools && needsServiceResult(request) && !inServicePlace(request) && (
                        <button className="btn btn-primary" onClick={() => setRelocating(request)}>
                          Move to service…
                        </button>
                      )}
                      {canHandleTools && request.state === "replaced" && toolReady && (
                        <button className="btn btn-primary" onClick={() => setStoring(request)}>
                          Store in rack…
                        </button>
                      )}
                      {canTake && request.state === "attending" && isProgram && mine && (
                        <button className="btn btn-primary" onClick={() => setResolving(request)}>
                          Resolve…
                        </button>
                      )}
                      {canTake && !done && (
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
      {!rows && !error && <p className="muted">Loading requests…</p>}

      {collecting && <CollectDialog request={collecting} destinations={destinations} onClose={() => setCollecting(null)} onDone={collected} />}
      {relocating && (
        <CollectDialog
          request={relocating}
          destinations={destinations}
          relocate
          onClose={() => setRelocating(null)}
          onDone={(request) => {
            setRelocating(null);
            setMessage(`${request.tool_id} moved to ${request.destination}. Now record the result.`);
            void load();
          }}
        />
      )}
      {finishing && (
        <FinishDialog
          request={finishing}
          onClose={() => setFinishing(null)}
          onDone={(request) => {
            setFinishing(null);
            setMessage(
              request.service?.result === "retired"
                ? `${request.tool_id} retired.`
                : request.state === "collected"
                  ? `${request.tool_id} is ready. Take it back to ${request.machine} or send another tool of TN ${request.tn}.`
                  : `${request.tool_id} is ready. Store it in its rack to close ${request.folio}.`,
            );
            void load();
          }}
        />
      )}
      {storing && (
        <StoreDialog
          request={storing}
          racks={destinations.filter((item) => item.tipo === "rack")}
          onClose={() => setStoring(null)}
          onDone={(request) => {
            setStoring(null);
            setMessage(`${request.tool_id} stored. ${request.folio} is closed.`);
            void load();
          }}
        />
      )}
      {recording && (
        <IncidentDialog
          request={recording}
          onClose={() => setRecording(null)}
          onDone={(request) => {
            setRecording(null);
            setMessage(`Incident of ${request.tool_id} saved.`);
            void load();
          }}
        />
      )}
      {resolving && (
        <ResolveDialog
          request={resolving}
          onClose={() => setResolving(null)}
          onDone={(request) => {
            setResolving(null);
            setMessage(`${request.folio} closed.`);
            void load();
          }}
        />
      )}
      {cancelling && (
        <ReasonDialog
          title={`Cancel ${cancelling.folio}`}
          message={
            cancelling.state === "collected"
              ? "The old tool was already recovered, so the position stays empty. Manufacturing can open a new tool request."
              : "If the tool is still on the machine it goes back to its position."
          }
          confirmLabel="Cancel request"
          danger
          onConfirm={(reason) => void cancel(cancelling, reason)}
          onClose={() => setCancelling(null)}
        />
      )}
    </section>
  );
}
