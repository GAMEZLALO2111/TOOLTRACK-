import { Fragment, useEffect, useMemo, useState } from "react";
import { api, ApiError, download } from "../api";
import { ErrorNote } from "../components/Dialog";

interface MachineOption {
  code: string;
  type: string | null;
  disabled?: unknown;
}
interface CatalogPart {
  numero_parte: string;
  types: string[];
}
interface Assignment {
  station: string;
  station_size: string | null;
  autoindex: boolean;
  tn: string;
  also: string[];
  tool_size: string | null;
  adapter: boolean;
  size_known: boolean;
  parts: string[];
  kept_by: number;
  of: number;
}
interface Unplaced {
  tn: string;
  also: string[];
  size: string | null;
  parts: string[];
  reason: string;
}
interface DesignPart {
  part: string;
  thickness: string | null;
  changes: number;
  total: number;
  off_standard: number;
  rows: { tn: string; angle: string | null; current_station: string; new_station: string | null; changed: boolean; off_standard: boolean; substitute_tn: string | null; difference: string | null }[];
}
interface Design {
  machine: string;
  machine_type: string;
  stations_available: number;
  stations_damaged: number;
  tools: number;
  placed: number;
  tool_merges: number;
  total_changes: number;
  total_rows: number;
  free_stations: Record<string, number>;
  assignments: Assignment[];
  unplaced: Unplaced[];
  parts: DesignPart[];
  suggestions: { remove: string; unplaced_after: number; parts_left: number }[];
  warnings: string[];
  skipped: { part: string; reason: string }[];
  tolerance: number;
}

const REASON: Record<string, string> = {
  no_free_station: "No free station left",
  no_compatible_station: "No station of its size",
};
const message = (caught: unknown, fallback: string) => (caught instanceof ApiError ? caught.message : fallback);

export function StandardTab() {
  const [machines, setMachines] = useState<MachineOption[]>([]);
  const [catalog, setCatalog] = useState<CatalogPart[]>([]);
  const [machine, setMachine] = useState("");
  const [parts, setParts] = useState<string[]>([]);
  const [typed, setTyped] = useState("");
  const [design, setDesign] = useState<Design | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<{ machines: MachineOption[] }>("GET", "/planeacion/maquinas")
      .then((result) => setMachines(result.machines.filter((row) => row.type)))
      .catch((caught) => setError(message(caught, "Could not load the machines")));
    api<CatalogPart[]>("GET", "/planeacion/partes").then(setCatalog).catch(() => setCatalog([]));
  }, []);

  const type = machines.find((row) => row.code === machine)?.type ?? null;
  const options = useMemo(() => catalog.filter((part) => !type || part.types.includes(type)), [catalog, type]);
  const query = `maquina=${encodeURIComponent(machine)}&partes=${encodeURIComponent(parts.join(","))}`;

  function add(text: string) {
    const found = text
      .split(/[\s,;]+/)
      .map((part) => part.trim().toUpperCase())
      .filter(Boolean);
    if (found.length) setParts((list) => [...list, ...found.filter((part) => !list.includes(part))]);
    setTyped("");
  }

  async function fromProposal() {
    setError(null);
    setInfo(null);
    try {
      const rows = await api<{ part: string; pieces: number }[]>("GET", `/planeacion/estandar/propuesta?maquina=${encodeURIComponent(machine)}`);
      if (rows.length === 0) return setInfo(`The planning proposal does not send any part to ${machine}.`);
      setParts(rows.map((row) => row.part));
      setInfo(`${rows.length} parts taken from the planning proposal for ${machine}.`);
    } catch (caught) {
      setError(message(caught, "Could not read the planning proposal"));
    }
  }

  async function run() {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      setDesign(await api<Design>("GET", `/planeacion/estandar?${query}`));
    } catch (caught) {
      setDesign(null);
      setError(message(caught, "Could not design the standard"));
    } finally {
      setBusy(false);
    }
  }

  async function excel() {
    setError(null);
    try {
      await download(`/planeacion/estandar/excel?${query}`, `standard-${machine}.xlsx`);
    } catch (caught) {
      setError(message(caught, "The Excel file could not be created"));
    }
  }

  return (
    <div>
      <p className="muted small">
        Proposes the station of each tool number so the programmers edit as few .stp lines as possible when they build the standard in Radan. It only proposes; nothing is applied to a machine or a program.
      </p>
      <div className="form-row">
        <select value={machine} onChange={(event) => { setMachine(event.target.value); setDesign(null); }} aria-label="Machine">
          <option value="">Machine…</option>
          {machines.map((row) => (
            <option key={row.code} value={row.code}>
              {row.code} · {row.type}
              {row.disabled ? " · DISABLED" : ""}
            </option>
          ))}
        </select>
        <input list="standard-parts" value={typed} onChange={(event) => setTyped(event.target.value)} onKeyDown={(event) => event.key === "Enter" && add(typed)} placeholder="Add part numbers (comma separated)" aria-label="Part number" style={{ minWidth: 280 }} />
        <datalist id="standard-parts">
          {options.map((part) => (
            <option key={part.numero_parte} value={part.numero_parte} />
          ))}
        </datalist>
        <button className="btn" onClick={() => add(typed)} disabled={!typed.trim()}>
          Add
        </button>
        <button className="btn" onClick={() => void fromProposal()} disabled={!machine} title="Take the parts the planning proposal sends to this machine">
          Use planning proposal
        </button>
      </div>
      {parts.length > 0 && (
        <div className="chips-row">
          {parts.map((part) => (
            <button key={part} className="chip chip-btn lvl-PARO" title="Remove" onClick={() => setParts((list) => list.filter((item) => item !== part))}>
              {part} ✕
            </button>
          ))}
          <button className="btn" onClick={() => { setParts([]); setDesign(null); }}>
            Clear
          </button>
        </div>
      )}
      <div className="kit-actions">
        <button className="btn btn-primary" disabled={busy || !machine || parts.length === 0} onClick={() => void run()}>
          {busy ? "Designing…" : `Design standard (${parts.length} parts)`}
        </button>
        {design && (
          <button className="btn" onClick={() => void excel()}>
            Download .xlsx
          </button>
        )}
      </div>
      <ErrorNote message={error} />
      {info && <div className="note note-info">{info}</div>}

      {design && (
        <>
          <div className="summary">
            <span className="chip sum lvl-PARO">{design.stations_available} stations{design.stations_damaged ? ` (${design.stations_damaged} damaged)` : ""}</span>
            <span className="chip sum lvl-PARO">{design.tools} tools needed</span>
            <span className={`chip sum ${design.unplaced.length ? "lvl-ROJO" : "lvl-VERDE"}`}>{design.placed} placed{design.unplaced.length ? ` · ${design.unplaced.length} do not fit` : ""}</span>
            <span className="chip sum lvl-AMARILLO">{design.total_changes} of {design.total_rows} .stp lines change</span>
            {design.tool_merges > 0 && <span className="chip sum lvl-VERDE">{design.tool_merges} TN merged by ±{design.tolerance} tolerance</span>}
            <span className="chip sum lvl-PARO">Free: {Object.entries(design.free_stations).map(([size, count]) => `${size} ${count}`).join(" · ") || "none"}</span>
          </div>
          {design.warnings.map((text) => (
            <div key={text} className="note note-warn">{text}</div>
          ))}
          {design.skipped.length > 0 && <div className="note note-warn">Skipped: {design.skipped.map((row) => `${row.part} (${row.reason})`).join(", ")}</div>}

          {design.unplaced.length > 0 && (
            <>
              <h3 className="q-done-title">Does not fit ({design.unplaced.length})</h3>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>TN</th>
                      <th>Size</th>
                      <th>Why</th>
                      <th>Parts</th>
                    </tr>
                  </thead>
                  <tbody>
                    {design.unplaced.map((row) => (
                      <tr key={`${row.tn}-${row.parts.join()}`}>
                        <td>
                          <strong>{row.tn}</strong>
                          {row.also.length > 0 && <div className="muted small">also {row.also.join(", ")}</div>}
                        </td>
                        <td>{row.size ?? "?"}</td>
                        <td>{REASON[row.reason] ?? row.reason}</td>
                        <td className="small">{row.parts.join(", ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {design.suggestions.length > 0 && (
                <div className="note note-info">
                  <strong>To fit more, leave these parts out of the standard (in this order):</strong>
                  <ol>
                    {design.suggestions.map((step) => (
                      <li key={step.remove}>
                        {step.remove} → {step.unplaced_after === 0 ? "everything fits" : `${step.unplaced_after} tools still do not fit`} ({step.parts_left} parts left)
                      </li>
                    ))}
                  </ol>
                </div>
              )}
            </>
          )}

          <h3 className="q-done-title">Standard turret ({design.placed} stations)</h3>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Station</th>
                  <th>TN</th>
                  <th>Tool size</th>
                  <th>Parts</th>
                  <th>Kept as in .stp</th>
                </tr>
              </thead>
              <tbody>
                {design.assignments.map((row) => (
                  <tr key={row.station}>
                    <td>
                      <strong>{row.station}</strong>
                      <div className="muted small">
                        size {row.station_size ?? "?"}
                        {row.autoindex ? " · autoindex" : ""}
                      </div>
                    </td>
                    <td>
                      <strong>{row.tn}</strong>
                      {row.also.length > 0 && <div className="muted small">also covers {row.also.join(", ")}</div>}
                    </td>
                    <td>
                      {row.tool_size ?? <span className="warn-text">?</span>}
                      {row.adapter && <span className="chip st-kitting">A in B adapter</span>}
                    </td>
                    <td>{row.of}</td>
                    <td>
                      {row.kept_by} of {row.of}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h3 className="q-done-title">What changes in each .stp</h3>
          <div className="table-wrap">
            <table className="table clickable-rows">
              <thead>
                <tr>
                  <th>Part</th>
                  <th>Thickness</th>
                  <th>Lines to change</th>
                  <th>Off standard</th>
                </tr>
              </thead>
              <tbody>
                {design.parts.map((part) => (
                  <Fragment key={part.part}>
                    <tr onClick={() => setOpen(open === part.part ? null : part.part)} tabIndex={0} onKeyDown={(event) => event.key === "Enter" && setOpen(open === part.part ? null : part.part)}>
                      <td>
                        <strong>{part.part}</strong>
                      </td>
                      <td>{part.thickness ?? "—"}</td>
                      <td>
                        {part.changes} of {part.total}
                      </td>
                      <td>{part.off_standard ? <span className="warn-text">{part.off_standard}</span> : "—"}</td>
                    </tr>
                    {open === part.part && (
                      <tr>
                        <td colSpan={4}>
                          <table className="table">
                            <thead>
                              <tr>
                                <th>TN in .stp</th>
                                <th>Angle</th>
                                <th>Station in .stp</th>
                                <th>Station in standard</th>
                                <th>Note</th>
                              </tr>
                            </thead>
                            <tbody>
                              {part.rows.map((row, index) => (
                                <tr key={`${row.tn}-${row.current_station}-${index}`} className={row.changed ? "wait-warn" : ""}>
                                  <td>{row.tn}</td>
                                  <td>{row.angle ?? "—"}</td>
                                  <td>{row.current_station}</td>
                                  <td>
                                    <strong>{row.new_station ?? "—"}</strong>
                                  </td>
                                  <td className="small">
                                    {row.off_standard && <span className="warn-text">Off standard: mount it by hand</span>}
                                    {row.changed && !row.off_standard && "Change the .stp"}
                                    {row.substitute_tn && ` · mount TN ${row.substitute_tn} (${row.difference} in.)`}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
