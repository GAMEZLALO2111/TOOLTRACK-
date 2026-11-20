import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { Dialog, ErrorNote } from "../components/Dialog";
import type { Program, ProgramLogRow, StrokeLimit, StrokeRow } from "../types";
import { fmt, PlanningBox, UploadDialog } from "./ProgramUpload";

export const ACTION_LABEL: Record<string, string> = { alta: "Created", actualizado: "Updated", created: "Created", updated: "Updated", uph: "UPH changed" };

function when(iso: string): string {
  return new Date(iso + "Z").toLocaleString();
}

export function ProgramsTab({ type }: { type: string }) {
  const { can } = useAuth();
  const [rows, setRows] = useState<Program[] | null>(null);
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [selected, setSelected] = useState<Program | null>(null);
  const [uploading, setUploading] = useState<Program | "new" | null>(null);
  const [limits, setLimits] = useState(false);

  const load = useCallback(async () => {
    try {
      setRows(await api<Program[]>("GET", `/workflow/programs?tipo_maquina=${encodeURIComponent(type)}`));
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load the programs.");
    }
  }, [type]);

  useEffect(() => {
    setRows(null);
    void load();
  }, [load]);

  const shown = useMemo(() => {
    const text = search.trim().toLowerCase();
    if (!text) return rows ?? [];
    return (rows ?? []).filter((r) => [r.numero_parte, r.cliente, r.familia, r.drawing_name].some((value) => value?.toLowerCase().includes(text)));
  }, [rows, search]);
  const missingPlanning = (rows ?? []).filter((r) => r.planeacion.cycle_con_lead_s === null).length;

  return (
    <div>
      <div className="summary">
        <span className="chip sum lvl-VERDE">{rows?.length ?? 0} part numbers for {type}</span>
        {missingPlanning > 0 && <span className="chip sum lvl-AMARILLO">{missingPlanning} without planning values</span>}
        {can("program.uph") && (
          <button className="btn push-right" onClick={() => setLimits(true)} title="Strokes before the first maintenance and between sharpenings, per tool number">
            Stroke limits
          </button>
        )}
        {can("program.manage") && (
          <button className="btn btn-primary" onClick={() => setUploading("new")}>
            Load a .stp
          </button>
        )}
      </div>
      <div className="filters">
        <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search part number, client, family or drawing" aria-label="Search programs" autoFocus />
      </div>
      <ErrorNote message={error} />
      {message && <div className="note note-info">✔ {message}</div>}
      {rows && shown.length === 0 && !error && <p className="note note-info">No programs match.</p>}
      {shown.length > 0 && (
        <div className="table-wrap">
          <table className="table clickable-rows">
            <thead>
              <tr>
                <th>Part number</th>
                <th>Client</th>
                <th>Family</th>
                <th>Type</th>
                <th>Rev</th>
                <th className="hide-sm">Thickness</th>
                <th className="hide-sm">Stations</th>
                <th className="hide-sm">Pcs/blank</th>
                <th className="hide-sm">Cycle (s)</th>
                <th className="hide-sm">UPH</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((p) => (
                <tr key={p.numero_parte} onClick={() => setSelected(p)} tabIndex={0} onKeyDown={(event) => event.key === "Enter" && setSelected(p)}>
                  <td>
                    <strong>{p.numero_parte}</strong>
                  </td>
                  <td>{p.cliente ?? "—"}</td>
                  <td>{p.familia ?? "—"}</td>
                  <td>{p.tipo_programa}</td>
                  <td>{p.revision}</td>
                  <td className="hide-sm">{p.thickness ?? "—"}</td>
                  <td className="hide-sm">{p.requisitos.length}</td>
                  <td className="hide-sm">{fmt(p.planeacion.piezas_blank)}</td>
                  <td className="hide-sm">{fmt(p.planeacion.cycle_time_s)}</td>
                  <td className="hide-sm">
                    {fmt(p.planeacion.uph_efectivo ?? p.planeacion.uph_teorico)}
                    {p.planeacion.uph_editado ? " ✎" : ""}
                  </td>
                  <td>
                    {when(p.actualizado_en)}
                    <div className="muted small">{p.actualizado_por}</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {selected && (
        <ProgramDialog
          program={selected}
          type={type}
          canManage={can("program.manage")}
          canUph={can("program.uph")}
          onClose={() => setSelected(null)}
          onUphSaved={(updated) => {
            setSelected(updated);
            setMessage(`UPH of ${updated.numero_parte} saved.`);
            void load();
          }}
          onRevise={() => {
            setUploading(selected);
            setSelected(null);
          }}
        />
      )}
      {limits && <LimitsDialog onClose={() => setLimits(false)} />}
      {uploading && (
        <UploadDialog
          type={type}
          existing={uploading === "new" ? null : uploading}
          onClose={() => setUploading(null)}
          onSaved={(part, revision) => {
            setUploading(null);
            setMessage(`${part} (${type}) saved as revision ${revision}.`);
            void load();
          }}
        />
      )}
    </div>
  );
}

function LimitsDialog({ onClose }: { onClose: () => void }) {
  const [rows, setRows] = useState<StrokeLimit[] | null>(null);
  const [tn, setTn] = useState("");
  const [category, setCategory] = useState("punzon");
  const [first, setFirst] = useState("");
  const [interval, setInterval_] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await api<StrokeLimit[]>("GET", "/workflow/strokes/limits"));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load the limits.");
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  async function save(limit: { tn: string; category: string; first: string; interval: string }) {
    const number = (text: string) => (text.trim() === "" ? null : Number(text));
    const values = [number(limit.first), number(limit.interval)];
    if (values.some((value) => value !== null && (!Number.isInteger(value) || value <= 0))) return setError("Limits are whole numbers of strokes above 0 (leave empty for no alert).");
    if (!limit.tn.trim()) return setError("Enter the TN.");
    setBusy(true);
    setError(null);
    try {
      await api("PUT", "/workflow/strokes/limits", { tn: limit.tn.trim(), categoria: limit.category, primer_mantenimiento: values[0], intervalo_afilado: values[1] });
      setTn("");
      setFirst("");
      setInterval_("");
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The limit could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog title="Stroke limits per tool number" onClose={onClose} wide>
      <p className="muted small">
        A tool is flagged at 90% of its limit and the system opens a sharpening request for Tool Crib at 100%. Before its first service the first-maintenance limit applies; afterwards, the sharpening interval. An empty value means no alert.
      </p>
      <div className="form-row">
        <input value={tn} onChange={(event) => setTn(event.target.value)} placeholder="TN" aria-label="TN" />
        <select value={category} onChange={(event) => setCategory(event.target.value)} aria-label="Category">
          <option value="punzon">Punch</option>
          <option value="matriz">Die</option>
        </select>
        <input inputMode="numeric" value={first} onChange={(event) => setFirst(event.target.value)} placeholder="First maintenance" aria-label="First maintenance" />
        <input inputMode="numeric" value={interval} onChange={(event) => setInterval_(event.target.value)} placeholder="Sharpening interval" aria-label="Sharpening interval" />
        <button className="btn btn-primary" disabled={busy} onClick={() => void save({ tn, category, first, interval })}>
          Save
        </button>
      </div>
      <ErrorNote message={error} />
      {rows && rows.length === 0 && <p className="note note-info">No limits yet.</p>}
      {rows && rows.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>TN</th>
                <th>Type</th>
                <th>First maintenance</th>
                <th>Sharpening interval</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.tn}/${row.category}`}>
                  <td>
                    <strong>{row.tn}</strong>
                  </td>
                  <td>{row.category === "punzon" ? "Punch" : "Die"}</td>
                  <td>{row.first_maintenance?.toLocaleString() ?? "—"}</td>
                  <td>{row.sharpening_interval?.toLocaleString() ?? "—"}</td>
                  <td>
                    <button
                      className="btn"
                      onClick={() => {
                        setTn(row.tn);
                        setCategory(row.category);
                        setFirst(row.first_maintenance?.toString() ?? "");
                        setInterval_(row.sharpening_interval?.toString() ?? "");
                      }}
                    >
                      Edit
                    </button>{" "}
                    <button className="btn btn-ghost-danger" disabled={busy} onClick={() => void save({ tn: row.tn, category: row.category, first: "", interval: "" })}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Dialog>
  );
}

function UphDialog({ program, type, onClose, onSaved }: { program: Program; type: string; onClose: () => void; onSaved: (program: Program) => void }) {
  const current = program.planeacion;
  const [value, setValue] = useState(String(current.uph_efectivo ?? current.uph_teorico ?? ""));
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(restore: boolean) {
    const amount = Number(value);
    if (!restore && (!Number.isFinite(amount) || amount <= 0)) return setError("Enter a UPH greater than 0.");
    if (reason.trim().length < 3) return setError("Write the reason for the change.");
    setBusy(true);
    setError(null);
    try {
      onSaved(await api<Program>("PUT", `/workflow/programs/${type}/${encodeURIComponent(program.numero_parte)}/uph`, { uph: restore ? null : amount, motivo: reason.trim(), usuario: "web" }));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The UPH could not be saved.");
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={`UPH · ${program.numero_parte}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Back
          </button>
          {current.uph_editado != null && (
            <button className="btn" onClick={() => void save(true)} disabled={busy}>
              Back to the .stp value ({fmt(current.uph_teorico)})
            </button>
          )}
          <button className="btn btn-primary" onClick={() => void save(false)} disabled={busy}>
            {busy ? "Saving…" : "Save UPH"}
          </button>
        </>
      }
    >
      <div className="form">
        <label>
          UPH used for the plan (the .stp says {fmt(current.uph_teorico)})
          <input inputMode="decimal" value={value} onChange={(event) => setValue(event.target.value)} autoFocus />
        </label>
        <label>
          Reason (required, it stays in the history)
          <textarea rows={2} maxLength={500} value={reason} onChange={(event) => setReason(event.target.value)} />
        </label>
        <ErrorNote message={error} />
      </div>
    </Dialog>
  );
}

function ProgramDialog({ program, type, canManage, canUph, onClose, onRevise, onUphSaved }: { program: Program; type: string; canManage: boolean; canUph: boolean; onClose: () => void; onRevise: () => void; onUphSaved: (program: Program) => void }) {
  const [log, setLog] = useState<ProgramLogRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editingUph, setEditingUph] = useState(false);
  const [strokes, setStrokes] = useState<Record<string, StrokeRow>>({});
  const [strokeEdits, setStrokeEdits] = useState<Record<string, string>>({});
  const [savingStrokes, setSavingStrokes] = useState(false);

  const loadStrokes = useCallback(async () => {
    try {
      const data = await api<{ rows: StrokeRow[] }>("GET", `/workflow/strokes/program/${type}/${encodeURIComponent(program.numero_parte)}`);
      setStrokes(Object.fromEntries(data.rows.map((row) => [`${row.station}|${row.tn}`, row])));
      setStrokeEdits({});
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load the strokes.");
    }
  }, [type, program.numero_parte]);

  useEffect(() => {
    void loadStrokes();
  }, [loadStrokes]);

  async function saveStrokes() {
    setError(null);
    const rows = Object.entries(strokeEdits).map(([key, text]) => {
      const [station, tn] = key.split("|");
      return { station, tn, strokes: text.trim() === "" ? null : Number(text) };
    });
    if (rows.some((row) => row.strokes !== null && !Number.isFinite(row.strokes))) return setError("Strokes per blank must be a number.");
    setSavingStrokes(true);
    try {
      await api("PUT", `/workflow/strokes/program/${type}/${encodeURIComponent(program.numero_parte)}`, { rows });
      await loadStrokes();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The strokes could not be saved.");
    } finally {
      setSavingStrokes(false);
    }
  }

  useEffect(() => {
    api<ProgramLogRow[]>("GET", `/workflow/programs/log?numero_parte=${encodeURIComponent(program.numero_parte)}&tipo_maquina=${encodeURIComponent(type)}`)
      .then(setLog)
      .catch((caught) => setError(caught instanceof ApiError ? caught.message : "Could not load the history."));
  }, [program.numero_parte, type, program.planeacion.uph_editado]);

  async function download() {
    setError(null);
    try {
      const full = await api<Program>("GET", `/workflow/programs/${type}/${encodeURIComponent(program.numero_parte)}`);
      const blob = new Blob([full.contenido ?? ""], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${program.numero_parte}.stp`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The file could not be downloaded.");
    }
  }

  return (
    <Dialog title={`${program.numero_parte} · ${type} · revision ${program.revision}`} onClose={onClose} wide>
      <div className="facts">
        <div>
          <div className="muted small">Client / family</div>
          {program.cliente ?? "—"} / {program.familia ?? "—"}
        </div>
        <div>
          <div className="muted small">Program type</div>
          {program.tipo_programa}
        </div>
        <div>
          <div className="muted small">Drawing</div>
          {program.drawing_name ?? "—"}
        </div>
        <div>
          <div className="muted small">Material · thickness</div>
          {program.material ?? "—"} · {program.thickness ?? "—"} in
        </div>
        <div>
          <div className="muted small">File</div>
          {program.nombre_archivo ?? "—"}
        </div>
        <div>
          <div className="muted small">Updated</div>
          {when(program.actualizado_en)} · {program.actualizado_por}
        </div>
      </div>
      <h3 className="q-done-title">Planning</h3>
      <PlanningBox planning={program.planeacion} />
      {program.planeacion.uph_editado != null && (
        <p className="muted small">
          UPH edited by {program.planeacion.uph_editado_por} {program.planeacion.uph_editado_en ? when(program.planeacion.uph_editado_en) : ""}: {program.planeacion.uph_motivo}
        </p>
      )}
      <div className="kit-actions">
        {canUph && (
          <button className="btn" onClick={() => setEditingUph(true)}>
            Edit UPH
          </button>
        )}
        <button className="btn" onClick={() => void download()}>
          Download .stp
        </button>
        {canManage && (
          <button className="btn btn-primary" onClick={onRevise}>
            Load a new revision
          </button>
        )}
      </div>
      {editingUph && (
        <UphDialog
          program={program}
          type={type}
          onClose={() => setEditingUph(false)}
          onSaved={(updated) => {
            setEditingUph(false);
            onUphSaved(updated);
          }}
        />
      )}
      <ErrorNote message={error} />
      <h3 className="q-done-title">Stations ({program.requisitos.length})</h3>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Station</th>
              <th>TN</th>
              <th>Angle</th>
              <th title="Strokes this station gives per blank (one cycle). The .stp column will fill it in later">Strokes per blank</th>
            </tr>
          </thead>
          <tbody>
            {program.requisitos.map((r) => (
              <tr key={`${r.station}-${r.tn}`}>
                <td>{r.station}</td>
                <td>
                  <strong>{r.tn}</strong>
                </td>
                <td>{r.angle ?? "—"}</td>
                <td>
                  {canUph ? (
                    <input
                      className="stroke-input"
                      inputMode="decimal"
                      value={strokeEdits[`${r.station}|${r.tn}`] ?? String(strokes[`${r.station}|${r.tn}`]?.strokes ?? "")}
                      onChange={(event) => setStrokeEdits({ ...strokeEdits, [`${r.station}|${r.tn}`]: event.target.value })}
                      aria-label={`Strokes per blank, station ${r.station}`}
                    />
                  ) : (
                    strokes[`${r.station}|${r.tn}`]?.strokes ?? "—"
                  )}
                  {strokes[`${r.station}|${r.tn}`]?.source === "default" && <span className="muted small"> default</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {canUph && Object.keys(strokeEdits).length > 0 && (
        <div className="kit-actions">
          <button className="btn btn-primary" disabled={savingStrokes} onClick={() => void saveStrokes()}>
            {savingStrokes ? "Saving…" : "Save strokes"}
          </button>
        </div>
      )}
      <h3 className="q-done-title">Change history</h3>
      {!log && !error && <p className="muted">Loading…</p>}
      {log && <LogTable rows={log} showPart={false} />}
    </Dialog>
  );
}

export function LogTable({ rows, showPart }: { rows: ProgramLogRow[]; showPart: boolean }) {
  if (rows.length === 0) return <p className="muted">No changes recorded.</p>;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>When</th>
            {showPart && <th>Part number</th>}
            {showPart && <th>Type</th>}
            <th>Action</th>
            <th>Rev</th>
            <th>By</th>
            <th>Reason</th>
            <th>What changed</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              <td>{when(row.fecha)}</td>
              {showPart && (
                <td>
                  <strong>{row.numero_parte}</strong>
                </td>
              )}
              {showPart && <td>{row.tipo_maquina}</td>}
              <td>{ACTION_LABEL[row.accion] ?? row.accion}</td>
              <td>{row.revision}</td>
              <td>{row.usuario}</td>
              <td>{row.motivo ?? "—"}</td>
              <td className="pre-line muted">{row.detalle ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
