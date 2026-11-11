import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, download } from "../api";
import { useAuth } from "../auth";
import { ConfirmDialog, Dialog, ErrorNote } from "../components/Dialog";
import { LocationPicker } from "../components/LocationPicker";
import { Chip } from "../components/MachineCard";
import { RecordTable, type RecordColumn, type RecordRow } from "../components/RecordTable";
import { when } from "../components/Service";
import { CATEGORY_LABEL, TOOL_STATE_CLASS } from "../labels";
import { go } from "../router";
import type { InvOptions, InvRow, LocationRow, ToolRow } from "../types";
import { ToolForm } from "./Tools";

interface FileSection {
  label: string;
  columns: RecordColumn[];
  total: number;
  rows: RecordRow[];
  groups: { value: string; label: string }[];
}

interface ToolFile {
  tool: InvRow;
  where: ToolRow;
  summary: {
    movements: number;
    first_movement_at: string | null;
    last_movement_at: string | null;
    sharpenings?: number;
    ground_off?: number;
    abnormal?: number;
    incidents?: number;
    tickets?: number;
    open_ticket?: string | null;
    requests?: number;
  };
  sections: Record<string, FileSection>;
}

const SECTION_ORDER = ["movements", "requests", "incidents", "tickets", "services"];
const SERVICE_PLACES = ["togu", "maintenance", "workshop"];
const MOVE_TARGETS = ["rack", "togu", "maintenance", "workshop"];

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="muted small">{label}</div>
      <strong>{value}</strong>
    </div>
  );
}

function MoveDialog({ tool, onClose, onDone }: { tool: ToolRow; onClose: () => void; onDone: () => void }) {
  const [locations, setLocations] = useState<LocationRow[]>([]);
  const [destination, setDestination] = useState(tool.home_rack ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<LocationRow[]>("GET", "/workflow/locations")
      .then((all) => setLocations(all.filter((item) => item.activa && MOVE_TARGETS.includes(item.tipo) && item.codigo !== tool.location).sort((a, b) => a.codigo.localeCompare(b.codigo))))
      .catch(() => setLocations([]));
  }, [tool.location]);

  async function submit() {
    if (!destination) return setError("Choose where the tool goes.");
    setBusy(true);
    setError(null);
    try {
      await api("POST", `/workflow/tools/${encodeURIComponent(tool.tool_id)}/move`, { destino_codigo: destination, realizado_por: "web" });
      onDone();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The movement could not be saved.");
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={`Move ${tool.tool_id}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Back
          </button>
          <button className="btn btn-primary" onClick={() => void submit()} disabled={busy}>
            {busy ? "Saving…" : "Move tool"}
          </button>
        </>
      }
    >
      <div className="form">
        <Fact label="Now in" value={tool.location ?? "—"} />
        <LocationPicker label="Where does it go?" options={locations} value={destination} onChange={setDestination} autoFocus showType />
        <ErrorNote message={error} />
      </div>
    </Dialog>
  );
}

export function ToolFilePage({ toolId }: { toolId: string }) {
  const { can } = useAuth();
  const [file, setFile] = useState<ToolFile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [section, setSection] = useState("movements");
  const [group, setGroup] = useState("all");
  const [editing, setEditing] = useState<{ options: InvOptions; locations: LocationRow[]; tns: string[] } | null>(null);
  const [moving, setMoving] = useState(false);
  const [status, setStatus] = useState<"danado" | "disponible" | "baja" | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setFile(await api<ToolFile>("GET", `/workflow/tools/${encodeURIComponent(toolId)}/file`));
      setError(null);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) return go("/tools");
      setError(caught instanceof ApiError ? caught.message : "Could not load the tool.");
    }
  }, [toolId]);

  useEffect(() => {
    setFile(null);
    setSection("movements");
    setGroup("all");
    void load();
  }, [load]);

  async function openForm() {
    setError(null);
    try {
      const [options, locations, tns] = await Promise.all([
        api<InvOptions>("GET", "/inventario/opciones"),
        api<LocationRow[]>("GET", "/workflow/locations"),
        api<{ tn: string }[]>("GET", "/catalogo/tn"),
      ]);
      setEditing({ options, locations: locations.filter((item) => item.activa), tns: tns.map((row) => row.tn) });
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not open the tool data.");
    }
  }

  async function changeStatus(next: "danado" | "disponible" | "baja") {
    setStatus(null);
    setBusy(true);
    setError(null);
    try {
      await api("POST", `/workflow/tools/${encodeURIComponent(toolId)}/status`, { estado: next, realizado_por: "web" });
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The change could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  async function save(sectionKey: string) {
    setBusy(true);
    setError(null);
    try {
      const params = new URLSearchParams({ tool: toolId, exact_tool: "true" });
      if (sectionKey === "movements") params.set("group", group);
      await download(`/records/${sectionKey}/download?${params.toString()}`, `MIRA-${toolId}-${sectionKey}.xlsx`);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The file could not be downloaded.");
    } finally {
      setBusy(false);
    }
  }

  const tabs = useMemo(() => SECTION_ORDER.filter((key) => file?.sections[key]), [file]);
  const current = file?.sections[section] ?? (file ? file.sections.movements : undefined);
  const rows = useMemo(() => {
    if (!current) return [];
    if (section !== "movements" || group === "all") return current.rows;
    const label = current.groups.find((item) => item.value === group)?.label;
    return current.rows.filter((row) => row.group === label);
  }, [current, section, group]);

  if (!file) {
    return (
      <section className="page">
        <div className="board-bar">
          <h1>Tool {toolId}</h1>
          <div className="bar-right">
            <button className="btn" onClick={() => go("/tools")}>
              ← Tools
            </button>
          </div>
        </div>
        <ErrorNote message={error} />
        {!error && <p className="muted">Loading…</p>}
      </section>
    );
  }

  const { tool, where, summary } = file;
  const retired = tool.estado === "baja";
  const inService = !!where.location_type && SERVICE_PLACES.includes(where.location_type);
  const ticketOpen = !!summary.open_ticket;
  const sectionSummary: Record<string, string> = {
    movements: String(summary.movements),
    requests: summary.requests !== undefined ? String(summary.requests) : "",
    incidents: summary.incidents !== undefined ? String(summary.incidents) : "",
    tickets: summary.tickets !== undefined ? String(summary.tickets) : "",
    services: summary.sharpenings !== undefined ? String(file.sections.services?.total ?? 0) : "",
  };

  return (
    <section className="page">
      <div className="board-bar">
        <h1>
          Tool {tool.id} <Chip text={where.estado_label} className={TOOL_STATE_CLASS[where.estado] ?? ""} />
        </h1>
        <div className="bar-right">
          <button className="btn" onClick={() => go("/tools")}>
            ← Tools
          </button>
          <button className="btn" onClick={() => void openForm()}>
            {can("inventory.edit") ? "Edit data" : "View data"}
          </button>
          {can("tools.move") && inService && !ticketOpen && (
            <button className="btn btn-primary" onClick={() => setMoving(true)}>
              Move…
            </button>
          )}
        </div>
      </div>
      <ErrorNote message={error} />
      {ticketOpen && (
        <div className="note note-warn">
          Open maintenance ticket {summary.open_ticket}.{" "}
          {can("maintenance.view") && (
            <button className="link-btn" onClick={() => go("/maintenance")}>
              Go to Maintenance
            </button>
          )}
        </div>
      )}
      <div className="facts">
        <Fact label="Where it is" value={where.status} />
        <Fact label="TN" value={tool.tn ?? "—"} />
        <Fact label="Type" value={`${CATEGORY_LABEL[tool.categoria] ?? tool.categoria}${tool.type ? ` · ${tool.type}` : ""}${tool.size ? ` · size ${tool.size}` : ""}`} />
        <Fact label="Home rack" value={where.home_rack ?? "—"} />
        {tool.clearance && <Fact label="Clearance" value={tool.clearance} />}
        <Fact label="Life" value={tool.life ?? "—"} />
        {tool.so && <Fact label="SO" value={tool.so} />}
        {where.drawing_name && <Fact label="On part number" value={`${where.drawing_name}${where.station ? ` · station ${where.station}` : ""}`} />}
        <Fact label="Catalog" value={tool.catalog_linked ? "Linked" : "Not linked"} />
        <Fact label="Last movement" value={when(summary.last_movement_at)} />
      </div>
      {summary.sharpenings !== undefined && (
        <div className="summary">
          <span className="chip sum lvl-PARO">{summary.movements} movements</span>
          <span className="chip sum lvl-VERDE">{summary.sharpenings} sharpenings</span>
          <span className="chip sum lvl-VERDE">{(summary.ground_off ?? 0).toFixed(3)} in. ground off</span>
          {!!summary.abnormal && <span className="chip sum lvl-ROJO">{summary.abnormal} abnormal</span>}
          <span className="chip sum lvl-AMARILLO">{summary.incidents} incidents</span>
          <span className="chip sum lvl-AMARILLO">{summary.tickets} tickets</span>
        </div>
      )}
      {can("tools.status") && !retired && (
        <div className="kit-actions">
          {where.estado !== "danado" && (
            <button className="btn" disabled={busy || ticketOpen} onClick={() => void changeStatus("danado")}>
              Mark as damaged
            </button>
          )}
          {where.estado !== "disponible" && (
            <button className="btn" disabled={busy || ticketOpen} onClick={() => void changeStatus("disponible")}>
              Release (available)
            </button>
          )}
          <button className="btn btn-ghost-danger" disabled={busy || ticketOpen} onClick={() => setStatus("baja")}>
            Retire
          </button>
        </div>
      )}
      <div className="tabs-inline wrap">
        {tabs.map((key) => (
          <button key={key} className={`btn${key === section ? " btn-primary" : ""}`} onClick={() => setSection(key)}>
            {file.sections[key].label}
            {sectionSummary[key] ? ` (${sectionSummary[key]})` : ""}
          </button>
        ))}
      </div>
      {section === "movements" && current && (
        <div className="tabs-inline wrap">
          {current.groups.map((item) => (
            <button key={item.value} className={`btn${item.value === group ? " btn-primary" : ""}`} onClick={() => setGroup(item.value)}>
              {item.label}
            </button>
          ))}
        </div>
      )}
      {current && (
        <>
          <div className="filters">
            <span className="muted push-right">{rows.length} of {current.total}</span>
            {can("records.view") && (
              <button className="btn" disabled={busy} onClick={() => void save(section)}>
                Download .xlsx
              </button>
            )}
          </div>
          {rows.length === 0 ? <p className="note note-info">Nothing recorded yet.</p> : <RecordTable columns={current.columns} rows={rows} />}
        </>
      )}
      {editing && (
        <ToolForm
          tool={tool}
          options={editing.options}
          locations={editing.locations}
          tns={editing.tns}
          canEdit={can("inventory.edit")}
          canDelete={can("inventory.delete")}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void load();
          }}
        />
      )}
      {moving && (
        <MoveDialog
          tool={where}
          onClose={() => setMoving(false)}
          onDone={() => {
            setMoving(false);
            void load();
          }}
        />
      )}
      {status === "baja" && (
        <ConfirmDialog title="Retire tool" message={`Retire ${tool.id}? It can never be kitted again.`} confirmLabel="Retire" danger onClose={() => setStatus(null)} onConfirm={() => void changeStatus("baja")} />
      )}
    </section>
  );
}
