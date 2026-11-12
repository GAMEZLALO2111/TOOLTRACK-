import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { ConfirmDialog, ErrorNote } from "../components/Dialog";
import { LocationPicker } from "../components/LocationPicker";
import { ScanInput } from "../components/ScanInput";
import { CATEGORY_LABEL, RETURN_REASONS } from "../labels";
import { toolCode, type Capture } from "../scan";
import type { LocationRow, MachineTools } from "../types";

function useLocations() {
  const [locations, setLocations] = useState<LocationRow[]>([]);
  useEffect(() => {
    api<LocationRow[]>("GET", "/workflow/locations")
      .then((all) => setLocations(all.filter((l) => l.activa)))
      .catch(() => setLocations([]));
  }, []);
  return locations;
}

interface LogLine {
  ok: boolean;
  text: string;
}

export function StoreTab() {
  const locations = useLocations();
  const racks = useMemo(() => locations.filter((l) => l.tipo === "rack").sort((a, b) => a.codigo.localeCompare(b.codigo)), [locations]);
  const [rack, setRack] = useState("");
  const [toolId, setToolId] = useState("");
  const [capture, setCapture] = useState<Capture>("manual");
  const [result, setResult] = useState("");
  const [log, setLog] = useState<LogLine[]>([]);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!rack && racks.length > 0) setRack(racks[0].codigo);
  }, [racks, rack]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const id = toolCode(toolId);
    if (!rack) return setLog([{ ok: false, text: "Choose the rack first." }, ...log]);
    if (!id) return;
    setBusy(true);
    try {
      const response = await api<{ tool_id: string; tn: string; from: string; to: string; kind: string }>("POST", `/workflow/racks/${encodeURIComponent(rack)}/store`, {
        tool_id: id,
        realizado_por: "web",
        resultado: result || null,
        captura: capture,
      });
      const text =
        response.kind === "baja"
          ? `✔ ${response.tool_id} retired (it does not go back to a rack).`
          : response.kind === "placement"
            ? `✔ ${response.tool_id} (TN ${response.tn}) placed in ${response.to} · first location`
            : `✔ ${response.tool_id} (TN ${response.tn}) stored: ${response.from} → ${response.to}`;
      setLog([{ ok: true, text }, ...log].slice(0, 12));
      setToolId("");
      setResult("");
    } catch (caught) {
      setLog([{ ok: false, text: `${id}: ${caught instanceof ApiError ? caught.message : "it could not be stored."}` }, ...log].slice(0, 12));
      input.current?.select();
    } finally {
      setBusy(false);
      input.current?.focus();
    }
  }

  return (
    <form className="panel" onSubmit={submit}>
      <div className="form two-col">
        <LocationPicker label="1. Rack" options={racks} value={rack} onChange={(code) => { setRack(code); input.current?.focus(); }} />
        <label>
          2. Tool ID
          <ScanInput ref={input} value={toolId} onValue={(value, mode) => { setToolId(value); setCapture(mode); }} placeholder="Scan the tool, then Enter" autoFocus />
        </label>
        <label>
          Result, only if it comes from sharpening or damage
          <select value={result} onChange={(event) => setResult(event.target.value)}>
            <option value="">Not needed</option>
            <option value="disponible">Ready for use</option>
            <option value="baja">Retire it</option>
          </select>
        </label>
        <div className="end">
          <button className="btn btn-primary big" type="submit" disabled={busy || !toolId.trim()}>
            Store
          </button>
        </div>
      </div>
      {log.length > 0 && (
        <ul className="log" role="log">
          {log.map((line, index) => (
            <li key={index} className={line.ok ? "ok" : "bad"}>
              {line.text}
            </li>
          ))}
        </ul>
      )}
    </form>
  );
}

export function ReturnsTab() {
  const { can } = useAuth();
  const locations = useLocations();
  const machines = useMemo(() => locations.filter((l) => l.tipo === "machine").map((l) => l.codigo).sort(), [locations]);
  const [machine, setMachine] = useState("");
  const [data, setData] = useState<MachineTools | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [reason, setReason] = useState("afilado");
  const [destination, setDestination] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const reasonInfo = RETURN_REASONS.find((r) => r.value === reason) ?? RETURN_REASONS[1];
  const destinations = useMemo(() => locations.filter((l) => reasonInfo.types.includes(l.tipo)).sort((a, b) => a.codigo.localeCompare(b.codigo)), [locations, reasonInfo]);

  useEffect(() => {
    if (!destinations.some((d) => d.codigo === destination)) setDestination(destinations[0]?.codigo ?? "");
  }, [destinations, destination]);

  const load = useCallback(async (code: string) => {
    setData(null);
    setPicked(new Set());
    if (!code) return;
    try {
      setData(await api<MachineTools>("GET", `/workflow/machines/${encodeURIComponent(code)}/tools`));
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load the tools.");
    }
  }, []);

  function toggle(id: string) {
    const next = new Set(picked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setPicked(next);
  }

  async function send() {
    setConfirming(false);
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const response = await api<{ returned: string[]; destination: string; remaining: number }>("POST", `/workflow/machines/${encodeURIComponent(machine)}/return`, {
        tool_ids: [...picked],
        motivo: reason,
        destino_codigo: destination,
        realizado_por: "web",
        nota: note.trim() || null,
      });
      setMessage(`✔ ${response.returned.length} tool(s) sent to ${response.destination}. ${response.remaining} still mounted.`);
      setNote("");
      await load(machine);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The return could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  const canReturn = can("returns.operate");
  const tools = data?.tools ?? [];
  return (
    <div>
      <div className="start-row">
        <select
          value={machine}
          onChange={(event) => {
            setMachine(event.target.value);
            setMessage(null);
            void load(event.target.value);
          }}
          aria-label="Machine"
        >
          <option value="">Select a machine…</option>
          {machines.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        {data && <span className="muted">{data.drawing_name ? `Running ${data.drawing_name}` : "No part registered"}</span>}
      </div>
      <ErrorNote message={error} />
      {message && <div className="note note-info">{message}</div>}
      {data && tools.length === 0 && <p className="note note-info">No tools are registered on this machine.</p>}
      {tools.length > 0 && (
        <>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  {canReturn && (
                    <th>
                      <input type="checkbox" checked={picked.size === tools.length} onChange={(event) => setPicked(event.target.checked ? new Set(tools.map((t) => t.tool_id)) : new Set())} aria-label="Select all" />
                    </th>
                  )}
                  <th>Station</th>
                  <th>TN</th>
                  <th>Angle</th>
                  <th>Part</th>
                  <th>Tool</th>
                  <th className="hide-sm">Clearance</th>
                </tr>
              </thead>
              <tbody>
                {tools.map((t) => (
                  <tr key={t.tool_id} className={picked.has(t.tool_id) ? "row-picked" : ""}>
                    {canReturn && (
                      <td>
                        <input type="checkbox" checked={picked.has(t.tool_id)} onChange={() => toggle(t.tool_id)} aria-label={`Select ${t.tool_id}`} />
                      </td>
                    )}
                    <td>{t.station ?? "—"}</td>
                    <td>
                      <strong>{t.tn}</strong>
                    </td>
                    <td>{t.angle ?? "—"}</td>
                    <td>{CATEGORY_LABEL[t.category] ?? t.category}</td>
                    <td>{t.tool_id}</td>
                    <td className="hide-sm">{t.clearance ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {canReturn ? (
            <div className="panel">
              <h2>Send the selected tools ({picked.size})</h2>
              <div className="form two-col">
                <label>
                  Reason
                  <select value={reason} onChange={(event) => setReason(event.target.value)}>
                    {RETURN_REASONS.map((r) => (
                      <option key={r.value} value={r.value}>
                        {r.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Destination
                  <select value={destination} onChange={(event) => setDestination(event.target.value)}>
                    {destinations.length === 0 && <option value="">No destinations registered</option>}
                    {destinations.map((d) => (
                      <option key={d.codigo} value={d.codigo}>
                        {d.codigo} ({d.tipo})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="span-2">
                  Note (optional)
                  <input value={note} onChange={(event) => setNote(event.target.value)} />
                </label>
              </div>
              <div className="form-actions">
                <button className="btn btn-primary" disabled={busy || picked.size === 0 || !destination} onClick={() => setConfirming(true)}>
                  Send selected
                </button>
              </div>
            </div>
          ) : (
            <p className="muted">Your role can look at this but cannot record returns.</p>
          )}
        </>
      )}
      {confirming && (
        <ConfirmDialog
          title="Confirm the return"
          message={`Send ${picked.size} tool(s) from ${machine} to ${destination} (${reasonInfo.label.toLowerCase()})? They will no longer count as mounted.`}
          confirmLabel="Send"
          onClose={() => setConfirming(false)}
          onConfirm={() => void send()}
        />
      )}
    </div>
  );
}

