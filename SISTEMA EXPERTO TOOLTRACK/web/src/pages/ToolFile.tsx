import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, download } from "../api";
import { useAuth } from "../auth";
import { Dialog, ErrorNote } from "../components/Dialog";
import { LocationPicker } from "../components/LocationPicker";
import { Chip } from "../components/MachineCard";
import { RecordTable, type RecordColumn, type RecordRow } from "../components/RecordTable";
import { ToolImage } from "../components/ToolImage";
import { when } from "../components/Service";
import { CATEGORY_LABEL, TOOL_STATE_CLASS } from "../labels";
import { go } from "../router";
import type { InvOptions, InvRow, LocationRow, ToolRow } from "../types";
import { ToolForm } from "./Tools";
import { RetireToolDialog } from "./Maintenance";

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
    open_request?: string | null;
    retirements?: number;
    pending_retirement?: string | null;
    requests?: number;
  };
  sections: Record<string, FileSection>;
}

const SECTION_ORDER = ["movements", "requests", "incidents", "tickets", "retirements", "services"];
const SERVICE_PLACES = ["togu", "maintenance", "workshop"];
const TOOLCRIB_PLACES = ["rack", "togu", "maintenance", "workshop"];
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
  const [retiring, setRetiring] = useState(false);
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

  async function changeStatus(next: "danado" | "disponible") {
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
  const retirementPending = !!summary.pending_retirement;
  const heldByToolCrib = !!where.location_type && TOOLCRIB_PLACES.includes(where.location_type);
  const canRetire = can("service.attend") && can("tools.move") && heldByToolCrib && !ticketOpen && !retirementPending && !summary.open_request;
  const sectionSummary: Record<string, string> = {
    movements: String(summary.movements),
    requests: summary.requests !== undefined ? String(summary.requests) : "",
    incidents: summary.incidents !== undefined ? String(summary.incidents) : "",
    tickets: summary.tickets !== undefined ? String(summary.tickets) : "",
    retirements: summary.retirements !== undefined ? String(summary.retirements) : "",
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
      {summary.open_request && (
        <div className="note note-warn">
          Request {summary.open_request} is handling this tool; record its result, maintenance or retirement there.{" "}
          {can("service.view") && (
            <button className="link-btn" onClick={() => go("/requests")}>
              Go to Requests
            </button>
          )}
        </div>
      )}
      {retirementPending && (
        <div className="note note-warn">
          Retirement {summary.pending_retirement} is waiting for approval.{" "}
          {can("maintenance.view") && (
            <button className="link-btn" onClick={() => go("/maintenance")}>
              Go to Maintenance
            </button>
          )}
        </div>
      )}
      <div className="facts">
        <Fact label="TN" value={tool.tn ?? "—"} />
        <Fact label="Type" value={`${CATEGORY_LABEL[tool.categoria] ?? tool.categoria}${tool.type ? ` · ${tool.type}` : ""}${tool.size ? ` · size ${tool.size}` : ""}`} />
        <Fact label="Home rack" value={where.home_rack ?? "—"} />
        {tool.clearance && <Fact label="Clearance" value={tool.clearance} />}
        <Fact
          label={tool.categoria === "matriz" ? "Height" : "Life"}
          value={
            <>
              {tool.life ?? "—"} {tool.life_alert && <Chip text="At minimum" className="st-missing" />}
            </>
          }
        />
        {tool.altura_total && <Fact label="Measured height" value={`${tool.altura_total}${tool.stripper ? ` (stripper ${tool.stripper}${tool.stripper_custom ? ", own" : ""})` : ""}`} />}
        {tool.proveedor && <Fact label="Supplier" value={tool.proveedor} />}
        {tool.so && <Fact label="SO" value={tool.so} />}
        {where.drawing_name && <Fact label="On part number" value={`${where.drawing_name}${where.station ? ` · station ${where.station}` : ""}`} />}
        <Fact label="Catalog" value={tool.catalog_linked ? "Linked" : "Not linked"} />
        <Fact label="Last movement" value={when(summary.last_movement_at)} />
      </div>
      {tool.life_alert && (
        <div className="note note-warn">
          This {tool.categoria === "matriz" ? "die is at or under its minimum height" : "punch is at its minimum life"} ({tool.life_minimum ?? "—"} in). It can still be kitted and sent to a machine, but it is flagged for the next purchase.
        </div>
      )}
      <ToolImage toolId={tool.id} tn={tool.tn} source={tool.image ?? null} canEditTool={can("inventory.edit")} canEditTn={can("catalog.edit")} onChanged={() => void load()} />
      {summary.sharpenings !== undefined && (
        <div className="summary">
          <span className="chip sum lvl-PARO">{summary.movements} movements</span>
          <span className="chip sum lvl-VERDE">{summary.sharpenings} sharpenings</span>
          <span className="chip sum lvl-VERDE">{(summary.ground_off ?? 0).toFixed(3)} in. ground off</span>
          {!!summary.abnormal && <span className="chip sum lvl-ROJO">{summary.abnormal} abnormal</span>}
          <span className="chip sum lvl-AMARILLO">{summary.incidents} incidents</span>
          <span className="chip sum lvl-AMARILLO">{summary.tickets} tickets</span>
          {!!summary.retirements && <span className="chip sum lvl-ROJO">{summary.retirements} retirements</span>}
        </div>
      )}
      {can("tools.status") && !retired && (
        <div className="kit-actions">
          {where.estado !== "danado" && (
            <button className="btn" disabled={busy || ticketOpen || retirementPending} onClick={() => void changeStatus("danado")}>
              Mark as damaged
            </button>
          )}
          {where.estado !== "disponible" && (
            <button className="btn" disabled={busy || ticketOpen || retirementPending} onClick={() => void changeStatus("disponible")}>
              Release (available)
            </button>
          )}
          {canRetire && (
            <button className="btn btn-ghost-danger" disabled={busy} onClick={() => setRetiring(true)}>
              Retire…
            </button>
          )}
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
      {retiring && (
        <RetireToolDialog
          toolId={tool.id}
          onClose={() => setRetiring(false)}
          onDone={() => {
            setRetiring(false);
            void load();
          }}
        />
      )}
    </section>
  );
}
