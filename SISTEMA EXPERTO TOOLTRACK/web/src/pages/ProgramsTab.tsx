import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { Dialog, ErrorNote } from "../components/Dialog";
import type { Program, ProgramLogRow } from "../types";
import { fmt, PlanningBox, UploadDialog } from "./ProgramUpload";

export const ACTION_LABEL: Record<string, string> = { alta: "Created", actualizado: "Updated", created: "Created", updated: "Updated" };

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
        {can("program.manage") && (
          <button className="btn btn-primary push-right" onClick={() => setUploading("new")}>
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
                  <td className="hide-sm">{fmt(p.planeacion.uph_teorico)}</td>
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
          onClose={() => setSelected(null)}
          onRevise={() => {
            setUploading(selected);
            setSelected(null);
          }}
        />
      )}
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

function ProgramDialog({ program, type, canManage, onClose, onRevise }: { program: Program; type: string; canManage: boolean; onClose: () => void; onRevise: () => void }) {
  const [log, setLog] = useState<ProgramLogRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<ProgramLogRow[]>("GET", `/workflow/programs/log?numero_parte=${encodeURIComponent(program.numero_parte)}&tipo_maquina=${encodeURIComponent(type)}`)
      .then(setLog)
      .catch((caught) => setError(caught instanceof ApiError ? caught.message : "Could not load the history."));
  }, [program.numero_parte, type]);

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
      <PlanningBox planning={program.planeacion} />
      <div className="kit-actions">
        <button className="btn" onClick={() => void download()}>
          Download .stp
        </button>
        {canManage && (
          <button className="btn btn-primary" onClick={onRevise}>
            Load a new revision
          </button>
        )}
      </div>
      <ErrorNote message={error} />
      <h3 className="q-done-title">Stations ({program.requisitos.length})</h3>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Station</th>
              <th>TN</th>
              <th>Angle</th>
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
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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
