import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { ConfirmDialog, Dialog, ErrorNote } from "../components/Dialog";
import { Chip } from "../components/MachineCard";
import { CATEGORY_LABEL, TOOL_STATE_CLASS, TOOL_STATE_LABEL } from "../labels";
import type { InvOptions, InvPage, InvRow, LocationRow } from "../types";

const PAGE_SIZE = 50;
const CATEGORIES = ["punzon", "matriz", "holder", "accesorio"];

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

export function ToolsTab() {
  const { can } = useAuth();
  const [options, setOptions] = useState<InvOptions | null>(null);
  const [locations, setLocations] = useState<LocationRow[]>([]);
  const [tns, setTns] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [state, setState] = useState("");
  const [link, setLink] = useState("");
  const [location, setLocation] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<InvPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<InvRow | "new" | null>(null);
  const debounced = useDebounced(search, 300);

  useEffect(() => {
    api<InvOptions>("GET", "/inventario/opciones").then(setOptions).catch(() => setOptions(null));
    api<LocationRow[]>("GET", "/workflow/locations").then((all) => setLocations(all.filter((l) => l.activa))).catch(() => setLocations([]));
    api<{ tn: string }[]>("GET", "/catalogo/tn").then((rows) => setTns(rows.map((r) => r.tn))).catch(() => setTns([]));
  }, []);

  useEffect(() => setOffset(0), [debounced, category, state, link, location]);

  const load = useCallback(async () => {
    setLoading(true);
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (debounced.trim()) params.set("q", debounced.trim());
    if (category) params.set("categoria", category);
    if (state) params.set("estado", state);
    if (link) params.set("vinculo", link);
    if (location) params.set("ubicacion", location);
    try {
      setData(await api<InvPage>("GET", `/inventario/herramientas?${params.toString()}`));
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load the inventory.");
    } finally {
      setLoading(false);
    }
  }, [debounced, category, state, link, location, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  const canEdit = can("inventory.edit");
  const places = useMemo(() => [...locations].sort((a, b) => a.codigo.localeCompare(b.codigo)), [locations]);
  const from = data && data.total > 0 ? data.offset + 1 : 0;
  const to = data ? Math.min(data.offset + data.limit, data.total) : 0;

  return (
    <div>
      {data && (
        <div className="summary">
          <span className="chip sum lvl-VERDE">{data.summary.total} physical tools</span>
          <span className="chip sum lvl-PARO">{data.summary.linked} linked to the catalog</span>
          <button className="chip sum lvl-AMARILLO chip-btn" onClick={() => setLink(link === "unlinked" ? "" : "unlinked")} title="Show only the tools not linked to the catalog">
            {data.summary.unlinked} not linked
          </button>
          {canEdit && (
            <button className="btn btn-primary push-right" onClick={() => setEditing("new")}>
              Register tool
            </button>
          )}
        </div>
      )}
      <div className="filters">
        <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search ID, TN, type, SO or location" aria-label="Search" autoFocus />
        <select value={category} onChange={(event) => setCategory(event.target.value)} aria-label="Category">
          <option value="">All categories</option>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {CATEGORY_LABEL[c]}
            </option>
          ))}
        </select>
        <select value={state} onChange={(event) => setState(event.target.value)} aria-label="State">
          <option value="">All states</option>
          {Object.entries(TOOL_STATE_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <select value={location} onChange={(event) => setLocation(event.target.value)} aria-label="Location">
          <option value="">All locations</option>
          {places.map((l) => (
            <option key={l.codigo} value={l.codigo}>
              {l.codigo}
            </option>
          ))}
        </select>
        <select value={link} onChange={(event) => setLink(event.target.value)} aria-label="Catalog link">
          <option value="">Linked or not</option>
          <option value="linked">Linked</option>
          <option value="unlinked">Not linked</option>
        </select>
      </div>
      <ErrorNote message={error} />
      {data && data.items.length === 0 && !error && <p className="note note-info">No tools match these filters.</p>}
      {data && data.items.length > 0 && (
        <>
          <div className="table-wrap">
            <table className="table clickable-rows">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Category</th>
                  <th>TN</th>
                  <th>Size</th>
                  <th>Type</th>
                  <th className="hide-sm">Clearance</th>
                  <th className="hide-sm">Life</th>
                  <th className="hide-sm">SO</th>
                  <th>Location</th>
                  <th>State</th>
                  <th className="hide-sm">Catalog</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((row) => (
                  <tr key={row.id} className={row.estado === "baja" ? "inactive" : ""} onClick={() => setEditing(row)} tabIndex={0} onKeyDown={(event) => event.key === "Enter" && setEditing(row)}>
                    <td>
                      <strong>{row.id}</strong>
                    </td>
                    <td>{CATEGORY_LABEL[row.categoria] ?? row.categoria}</td>
                    <td>{row.tn ?? "—"}</td>
                    <td>{row.size ?? "—"}</td>
                    <td>{row.type ?? "—"}</td>
                    <td className="hide-sm">{row.clearance ?? "—"}</td>
                    <td className="hide-sm">{row.life ?? "—"}</td>
                    <td className="hide-sm">{row.so ?? "—"}</td>
                    <td>{row.ubicacion ?? "—"}</td>
                    <td>
                      <Chip text={TOOL_STATE_LABEL[row.estado] ?? row.estado} className={TOOL_STATE_CLASS[row.estado] ?? ""} />
                    </td>
                    <td className="hide-sm">{row.catalog_linked ? "Linked" : row.categoria === "punzon" || row.categoria === "matriz" ? <span className="warn-text">Not linked</span> : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <span className="muted">
              {from}–{to} of {data.total}
              {loading ? " · loading…" : ""}
            </span>
            <div>
              <button className="btn" disabled={data.offset === 0} onClick={() => setOffset(Math.max(0, data.offset - PAGE_SIZE))}>
                ← Previous
              </button>
              <button className="btn" disabled={to >= data.total} onClick={() => setOffset(data.offset + PAGE_SIZE)}>
                Next →
              </button>
            </div>
          </div>
        </>
      )}
      {editing && options && (
        <ToolForm
          tool={editing === "new" ? null : editing}
          options={options}
          locations={places}
          tns={tns}
          canEdit={canEdit}
          canDelete={can("inventory.delete")}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void load();
          }}
        />
      )}
    </div>
  );
}

interface FormProps {
  tool: InvRow | null;
  options: InvOptions;
  locations: LocationRow[];
  tns: string[];
  canEdit: boolean;
  canDelete: boolean;
  onClose: () => void;
  onSaved: () => void;
}

function ToolForm({ tool, options, locations, tns, canEdit, canDelete, onClose, onSaved }: FormProps) {
  const isNew = tool === null;
  const [id, setId] = useState(tool?.id ?? "");
  const [category, setCategory] = useState(tool?.categoria ?? "punzon");
  const [tn, setTn] = useState(tool?.tn ?? "");
  const [size, setSize] = useState(tool?.size ?? "A");
  const [type, setType] = useState(tool?.type ?? "");
  const [life, setLife] = useState(tool?.life ?? "");
  const [clearance, setClearance] = useState(tool?.clearance ?? "");
  const [so, setSo] = useState(tool?.so ?? "");
  const [place, setPlace] = useState(tool?.ubicacion ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const cuts = category === "punzon" || category === "matriz";
  const types = options.tipos[category] ?? [];
  const readOnly = !canEdit;
  const placeKnown = !place || locations.some((l) => l.codigo === place);

  function changeCategory(next: string) {
    setCategory(next);
    setType("");
    if (next !== "matriz") setClearance("");
    if (next !== "punzon" && next !== "matriz") {
      setTn("");
      setSize("A");
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!id.trim()) return setError("Enter the physical tool ID.");
    if (!type) return setError("Choose the type.");
    setBusy(true);
    setError(null);
    const body = {
      id: id.trim(),
      categoria: category,
      type,
      tn: cuts ? tn.trim() : null,
      size: cuts ? size : null,
      life: life.trim() || null,
      clearance: category === "matriz" ? clearance.trim() : null,
      so: so.trim() || null,
      ubicacion: place || null,
    };
    try {
      if (isNew) await api("POST", "/inventario/herramientas", body);
      else await api("PUT", `/inventario/herramientas/${encodeURIComponent(tool.id)}`, body);
      onSaved();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The tool could not be saved.");
      setBusy(false);
    }
  }

  async function remove() {
    setConfirmDelete(false);
    setBusy(true);
    try {
      await api("DELETE", `/herramientas/${encodeURIComponent(id)}`);
      onSaved();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The tool could not be deleted.");
      setBusy(false);
    }
  }

  return (
    <Dialog title={isNew ? "Register a physical tool" : `Tool ${tool.id}`} onClose={onClose} wide>
      <form className="form" onSubmit={submit}>
        <div className="two-col">
          <label>
            Physical ID
            <input value={id} onChange={(event) => setId(event.target.value)} disabled={!isNew} autoFocus={isNew} autoCapitalize="characters" />
          </label>
          <label>
            Category
            <select value={category} onChange={(event) => changeCategory(event.target.value)} disabled={readOnly}>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {CATEGORY_LABEL[c]}
                </option>
              ))}
            </select>
          </label>
          {cuts && (
            <>
              <label>
                TN (tool number)
                <input list="inv-tns" value={tn} onChange={(event) => setTn(event.target.value)} disabled={readOnly} />
                <datalist id="inv-tns">
                  {tns.map((t) => (
                    <option key={t} value={t} />
                  ))}
                </datalist>
              </label>
              <label>
                Size
                <select value={size} onChange={(event) => setSize(event.target.value)} disabled={readOnly}>
                  {options.sizes.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}
          <label>
            Type
            <select value={type} onChange={(event) => setType(event.target.value)} disabled={readOnly}>
              <option value="">Select…</option>
              {tool?.type && !types.includes(tool.type) && <option value={tool.type}>{tool.type} (current)</option>}
              {types.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label>
            Life {cuts ? "" : "(optional)"}
            <input inputMode="decimal" value={life} onChange={(event) => setLife(event.target.value)} disabled={readOnly} placeholder="e.g. 10.000" />
          </label>
          {category === "matriz" && (
            <label>
              Clearance
              <input inputMode="decimal" value={clearance} onChange={(event) => setClearance(event.target.value)} disabled={readOnly} placeholder="e.g. 0.016" />
            </label>
          )}
          <label>
            SO
            <input value={so} onChange={(event) => setSo(event.target.value)} disabled={readOnly} />
          </label>
          <label>
            Location
            <select value={place} onChange={(event) => setPlace(event.target.value)} disabled={readOnly}>
              <option value="">No location</option>
              {!placeKnown && <option value={place}>{place} (not registered)</option>}
              {locations.map((l) => (
                <option key={l.codigo} value={l.codigo}>
                  {l.codigo} ({l.tipo})
                </option>
              ))}
            </select>
          </label>
        </div>
        {!isNew && <p className="muted small">The location and identity of a tool with movement history can only change through movements (kitting, returns, Tool Tracking). The state is changed in Tool Tracking.</p>}
        <ErrorNote message={error} />
        <div className="form-actions spread">
          {!isNew && canDelete ? (
            <button type="button" className="btn btn-ghost-danger" disabled={busy} onClick={() => setConfirmDelete(true)}>
              Delete
            </button>
          ) : (
            <span />
          )}
          <span className="pair">
            <button type="button" className="btn" onClick={onClose}>
              {readOnly ? "Close" : "Cancel"}
            </button>
            {canEdit && (
              <button type="submit" className="btn btn-primary" disabled={busy}>
                {isNew ? "Register" : "Save changes"}
              </button>
            )}
          </span>
        </div>
      </form>
      {confirmDelete && (
        <ConfirmDialog
          title="Delete tool"
          message={`Delete ${id}? This is only for entry mistakes; a tool with movement history cannot be deleted (retire it in Tool Tracking instead).`}
          confirmLabel="Delete"
          danger
          onClose={() => setConfirmDelete(false)}
          onConfirm={() => void remove()}
        />
      )}
    </Dialog>
  );
}
