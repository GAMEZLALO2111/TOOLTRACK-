import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { ConfirmDialog, ErrorNote } from "../components/Dialog";
import { CATEGORY_LABEL, RETURN_REASONS } from "../labels";
import type { LocationRow, MachineTools, MovableTool } from "../types";
import { usePoll } from "../usePoll";

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
  const [result, setResult] = useState("");
  const [log, setLog] = useState<LogLine[]>([]);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!rack && racks.length > 0) setRack(racks[0].codigo);
  }, [racks, rack]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const id = toolId.trim();
    if (!rack) return setLog([{ ok: false, text: "Choose the rack first." }, ...log]);
    if (!id) return;
    setBusy(true);
    try {
      const response = await api<{ tool_id: string; tn: string; from: string; to: string; kind: string }>("POST", `/workflow/racks/${encodeURIComponent(rack)}/store`, {
        tool_id: id,
        realizado_por: "web",
        resultado: result || null,
      });
      const text = response.kind === "baja" ? `✔ ${response.tool_id} retired (it does not go back to a rack).` : `✔ ${response.tool_id} (TN ${response.tn}) stored: ${response.from} → ${response.to}`;
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
      <p className="muted">Choose the rack and scan each tool that comes back (from a machine, pickups, TOGU, maintenance or the workshop). It is only accepted in its own rack.</p>
      <div className="form two-col">
        <label>
          1. Rack
          <select value={rack} onChange={(event) => { setRack(event.target.value); input.current?.focus(); }}>
            {racks.length === 0 && <option value="">No racks registered</option>}
            {racks.map((r) => (
              <option key={r.codigo} value={r.codigo}>
                {r.codigo}
              </option>
            ))}
          </select>
        </label>
        <label>
          2. Tool ID
          <input ref={input} value={toolId} onChange={(event) => setToolId(event.target.value)} placeholder="Scan the tool, then Enter" autoFocus autoComplete="off" autoCapitalize="characters" />
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
      <p className="muted">See what is mounted on a machine and send tools back: to their rack, to sharpening (TOGU or workshop) or to maintenance if they are damaged.</p>
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

export function ServiceTab() {
  const { can } = useAuth();
  const locations = useLocations();
  const [rows, setRows] = useState<MovableTool[]>([]);
  const [choice, setChoice] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await api<MovableTool[]>("GET", "/workflow/tools/movable"));
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load the tools.");
    }
  }, []);
  usePoll(load, 30000);

  const destinations = useMemo(() => locations.filter((l) => ["rack", "togu", "maintenance", "workshop"].includes(l.tipo)).sort((a, b) => a.codigo.localeCompare(b.codigo)), [locations]);

  async function move(row: MovableTool) {
    const destination = choice[row.tool_id] ?? row.home_rack ?? "";
    if (!destination) return setError("Choose where the tool goes.");
    setError(null);
    setMessage(null);
    try {
      await api("POST", `/workflow/tools/${encodeURIComponent(row.tool_id)}/move`, { destino_codigo: destination, realizado_por: "web" });
      setMessage(`✔ ${row.tool_id} moved to ${destination}.`);
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The movement could not be saved.");
    }
  }

  return (
    <div>
      <p className="muted">Tools in TOGU, maintenance or the workshop. Move them between those places or send them back to their rack (a tool coming from sharpening or damage is released in “Store in rack” with its result).</p>
      <ErrorNote message={error} />
      {message && <div className="note note-info">{message}</div>}
      {rows.length === 0 && !error && <p className="note note-info">No tools are in service right now.</p>}
      {rows.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Tool</th>
                <th>TN</th>
                <th>Part</th>
                <th>Now in</th>
                <th>Home rack</th>
                {can("tools.move") && <th>Move to</th>}
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.tool_id}>
                  <td>
                    <strong>{row.tool_id}</strong>
                  </td>
                  <td>{row.tn}</td>
                  <td>{CATEGORY_LABEL[row.category ?? ""] ?? row.category ?? "—"}</td>
                  <td>
                    {row.location} ({row.location_type})
                  </td>
                  <td>{row.home_rack ?? "—"}</td>
                  {can("tools.move") && (
                    <>
                      <td>
                        <select value={choice[row.tool_id] ?? row.home_rack ?? ""} onChange={(event) => setChoice({ ...choice, [row.tool_id]: event.target.value })} aria-label={`Destination for ${row.tool_id}`}>
                          <option value="">Select…</option>
                          {destinations.filter((d) => d.codigo !== row.location).map((d) => (
                            <option key={d.codigo} value={d.codigo}>
                              {d.codigo} ({d.tipo})
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="row-actions">
                        <button className="btn btn-primary" onClick={() => void move(row)}>
                          Move
                        </button>
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
