import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { Dialog, ErrorNote } from "../components/Dialog";
import { LifeSettingsDialog } from "../components/LifeSettingsDialog";
import { Chip } from "../components/MachineCard";
import { CATEGORY_LABEL, TOOL_STATE_CLASS, TOOL_STATE_LABEL } from "../labels";
import { go } from "../router";
import { toolCode } from "../scan";
import type { CatalogConfig, InvOptions, InvPage, InvRow, LocationRow } from "../types";

const PAGE_SIZE = 50;
const CATEGORIES = ["punzon", "matriz", "holder", "accesorio"];

type ColumnKey = "id" | "tn" | "type" | "size" | "clearance" | "life" | "so" | "supplier" | "alert";
const COLUMN_PARAMS: Record<ColumnKey, string> = {
  id: "f_id",
  tn: "f_tn",
  type: "f_type",
  size: "f_size",
  clearance: "f_clearance",
  life: "f_life",
  so: "f_so",
  supplier: "f_supplier",
  alert: "f_alert",
};

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

export function ToolsList() {
  const { can } = useAuth();
  const [options, setOptions] = useState<InvOptions | null>(null);
  const [locations, setLocations] = useState<LocationRow[]>([]);
  const [tns, setTns] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [state, setState] = useState("");
  const [link, setLink] = useState("");
  const [location, setLocation] = useState("");
  const [cols, setCols] = useState<Record<ColumnKey, string>>({ id: "", tn: "", type: "", size: "", clearance: "", life: "", so: "", supplier: "", alert: "" });
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<InvPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [registering, setRegistering] = useState(false);
  const [settings, setSettings] = useState(false);
  const debounced = useDebounced(toolCode(search) || search.trim(), 300);
  const debouncedCols = useDebounced(JSON.stringify(cols), 300);

  useEffect(() => {
    api<InvOptions>("GET", "/inventario/opciones").then(setOptions).catch(() => setOptions(null));
    api<LocationRow[]>("GET", "/workflow/locations").then((all) => setLocations(all.filter((l) => l.activa))).catch(() => setLocations([]));
    api<{ tn: string }[]>("GET", "/catalogo/tn").then((rows) => setTns(rows.map((r) => r.tn))).catch(() => setTns([]));
  }, []);

  useEffect(() => setOffset(0), [debounced, debouncedCols, category, state, link, location]);

  const load = useCallback(async () => {
    setLoading(true);
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (debounced) params.set("q", debounced);
    if (category) params.set("categoria", category);
    if (state) params.set("estado", state);
    if (link) params.set("vinculo", link);
    if (location) params.set("ubicacion", location);
    const filters = JSON.parse(debouncedCols) as Record<ColumnKey, string>;
    for (const [key, param] of Object.entries(COLUMN_PARAMS) as [ColumnKey, string][]) {
      if (filters[key]) params.set(param, filters[key]);
    }
    try {
      setData(await api<InvPage>("GET", `/inventario/herramientas?${params.toString()}`));
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load the inventory.");
    } finally {
      setLoading(false);
    }
  }, [debounced, debouncedCols, category, state, link, location, offset]);

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
            <button className="btn push-right" onClick={() => setSettings(true)} title="Stripper plates by size and minimum life">
              Life settings
            </button>
          )}
          {canEdit && (
            <button className="btn btn-primary" onClick={() => setRegistering(true)}>
              Register tool
            </button>
          )}
        </div>
      )}
      <form
        className="filters"
        onSubmit={(event) => {
          event.preventDefault();
          // A scan or an exact ID opens the tool file straight away.
          const exact = data?.items.find((row) => row.id.toUpperCase() === debounced.toUpperCase());
          const only = data && data.items.length === 1 ? data.items[0] : undefined;
          const target = exact ?? only;
          if (target) go(`/tools/${encodeURIComponent(target.id)}`);
        }}
      >
        <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Scan or type ID, TN, type, SO, supplier or location" aria-label="Search" autoFocus />
        {(Object.values(cols).some(Boolean) || category || state || location || link) && (
          <button
            type="button"
            className="btn"
            onClick={() => {
              setCols({ id: "", tn: "", type: "", size: "", clearance: "", life: "", so: "", supplier: "", alert: "" });
              setCategory("");
              setState("");
              setLocation("");
              setLink("");
            }}
          >
            Clear filters
          </button>
        )}
      </form>
      <ErrorNote message={error} />
      {data && (
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
                  <th className="hide-sm">Life / height</th>
                  <th className="hide-sm">SO</th>
                  <th className="hide-sm">Supplier</th>
                  <th>Location</th>
                  <th>State</th>
                  <th className="hide-sm">Catalog</th>
                </tr>
                <tr className="filter-row">
                  <th>
                    <input value={cols.id} onChange={(event) => setCols({ ...cols, id: event.target.value })} placeholder="Filter" aria-label="Filter by ID" />
                  </th>
                  <th>
                    <select value={category} onChange={(event) => setCategory(event.target.value)} aria-label="Filter by category">
                      <option value="">All</option>
                      {CATEGORIES.map((c) => (
                        <option key={c} value={c}>
                          {CATEGORY_LABEL[c]}
                        </option>
                      ))}
                    </select>
                  </th>
                  <th>
                    <input value={cols.tn} onChange={(event) => setCols({ ...cols, tn: event.target.value })} placeholder="Filter" aria-label="Filter by TN" />
                  </th>
                  <th>
                    <select value={cols.size} onChange={(event) => setCols({ ...cols, size: event.target.value })} aria-label="Filter by size">
                      <option value="">All</option>
                      {(options?.sizes ?? []).map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                  </th>
                  <th>
                    <select value={cols.type} onChange={(event) => setCols({ ...cols, type: event.target.value })} aria-label="Filter by type">
                      <option value="">All</option>
                      {(options?.tipos_en_uso ?? []).map((t) => (
                        <option key={t} value={t}>
                          {t}
                        </option>
                      ))}
                    </select>
                  </th>
                  <th className="hide-sm">
                    <select value={cols.clearance} onChange={(event) => setCols({ ...cols, clearance: event.target.value })} aria-label="Filter by clearance">
                      <option value="">All</option>
                      {(options?.claridades ?? []).map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  </th>
                  <th className="hide-sm">
                    <select value={cols.alert} onChange={(event) => setCols({ ...cols, alert: event.target.value })} aria-label="Filter by life">
                      <option value="">All</option>
                      <option value="low">At minimum</option>
                    </select>
                  </th>
                  <th className="hide-sm">
                    <input value={cols.so} onChange={(event) => setCols({ ...cols, so: event.target.value })} placeholder="Filter" aria-label="Filter by SO" />
                  </th>
                  <th className="hide-sm">
                    <select value={cols.supplier} onChange={(event) => setCols({ ...cols, supplier: event.target.value })} aria-label="Filter by supplier">
                      <option value="">All</option>
                      {(options?.proveedores ?? []).map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                  </th>
                  <th>
                    <select value={location} onChange={(event) => setLocation(event.target.value)} aria-label="Filter by location">
                      <option value="">All</option>
                      <option value="-">No location</option>
                      {places.map((l) => (
                        <option key={l.codigo} value={l.codigo}>
                          {l.codigo}
                        </option>
                      ))}
                    </select>
                  </th>
                  <th>
                    <select value={state} onChange={(event) => setState(event.target.value)} aria-label="Filter by state">
                      <option value="">All</option>
                      {Object.entries(TOOL_STATE_LABEL).map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </th>
                  <th className="hide-sm">
                    <select value={link} onChange={(event) => setLink(event.target.value)} aria-label="Filter by catalog link">
                      <option value="">All</option>
                      <option value="linked">Linked</option>
                      <option value="unlinked">Not linked</option>
                    </select>
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.items.length === 0 && (
                  <tr>
                    <td colSpan={12} className="muted">
                      No tools match these filters.
                    </td>
                  </tr>
                )}
                {data.items.map((row) => (
                  <tr key={row.id} className={row.estado === "baja" ? "inactive" : ""} onClick={() => go(`/tools/${encodeURIComponent(row.id)}`)} tabIndex={0} onKeyDown={(event) => event.key === "Enter" && go(`/tools/${encodeURIComponent(row.id)}`)}>
                    <td>
                      <strong>{row.id}</strong>
                    </td>
                    <td>{CATEGORY_LABEL[row.categoria] ?? row.categoria}</td>
                    <td>{row.tn ?? "—"}</td>
                    <td>{row.size ?? "—"}</td>
                    <td>{row.type ?? "—"}</td>
                    <td className="hide-sm">{row.clearance ?? "—"}</td>
                    <td className="hide-sm">
                      {row.life ?? "—"} {row.life_alert && <Chip text="At minimum" className="st-missing" />}
                    </td>
                    <td className="hide-sm">{row.so ?? "—"}</td>
                    <td className="hide-sm">{row.proveedor ?? "—"}</td>
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
      {settings && <LifeSettingsDialog onClose={() => setSettings(false)} onSaved={() => { setSettings(false); void load(); api<InvOptions>("GET", "/inventario/opciones").then(setOptions).catch(() => undefined); }} />}
      {registering && options && (
        <ToolForm
          tool={null}
          options={options}
          locations={places}
          tns={tns}
          canEdit={canEdit}
          onClose={() => setRegistering(false)}
          onSaved={() => {
            setRegistering(false);
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
  onClose: () => void;
  onSaved: () => void;
}

export function ToolForm({ tool, options, locations, tns, canEdit, onClose, onSaved }: FormProps) {
  const isNew = tool === null;
  const [id, setId] = useState(tool?.id ?? "");
  const [category, setCategory] = useState(tool?.categoria ?? "punzon");
  const [tn, setTn] = useState(tool?.tn ?? "");
  const [size, setSize] = useState(tool?.size ?? "A");
  const [type, setType] = useState(tool?.type ?? "");
  const [life, setLife] = useState(tool?.life ?? "");
  const [altura, setAltura] = useState(tool?.altura_total ?? "");
  const [stripper, setStripper] = useState(tool?.stripper_custom ? (tool?.stripper ?? "") : "");
  const [clearance, setClearance] = useState(tool?.clearance ?? "");
  const [so, setSo] = useState(tool?.so ?? "");
  const [supplier, setSupplier] = useState(tool?.proveedor ?? "");
  const [place, setPlace] = useState(tool?.ubicacion ?? "");
  const [configs, setConfigs] = useState<CatalogConfig[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<CatalogConfig[]>("GET", "/catalogo/configuraciones").then(setConfigs).catch(() => setConfigs([]));
  }, []);

  const cuts = category === "punzon" || category === "matriz";
  const types = options.tipos[category] ?? [];
  const readOnly = !canEdit;
  const racks = locations.filter((l) => l.tipo === "rack").sort((a, b) => a.codigo.localeCompare(b.codigo));
  // The catalog decides the type of a TN and size that already exist.
  const catalogType = cuts ? (configs.find((c) => c.tn === tn.trim() && c.size === size)?.type ?? null) : null;
  const shownType = catalogType ?? type;
  const defaultStripper = options.stripper_por_tamano?.[size] ?? "";
  const heightNumber = Number(altura);
  const stripperNumber = Number(stripper.trim() === "" ? defaultStripper : stripper);
  const usableLife = category === "punzon" && altura.trim() !== "" && Number.isFinite(heightNumber) && Number.isFinite(stripperNumber) ? heightNumber - stripperNumber : null;
  const legacyLife = category === "punzon" && !!tool && !tool.altura_total && !!tool.life;
  const minimum = category === "punzon" ? Number(options.vida_minima_punzon ?? "0.2") : Number(options.altura_minima_matriz ?? "1.05");
  const checkedLife = category === "punzon" ? (usableLife ?? (legacyLife ? Number(life) : null)) : life.trim() !== "" ? Number(life) : null;

  function changeCategory(next: string) {
    setCategory(next);
    setType("");
    setAltura("");
    setStripper("");
    if (next !== "matriz") setClearance("");
    if (next !== "punzon" && next !== "matriz") {
      setTn("");
      setSize("A");
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!id.trim()) return setError("Enter the physical tool ID.");
    if (!shownType) return setError("Choose the type.");
    if (category === "matriz" && !clearance) return setError("Choose the die clearance.");
    setBusy(true);
    setError(null);
    const punchByHeight = category === "punzon" && altura.trim() !== "";
    const body = {
      id: id.trim(),
      categoria: category,
      type: shownType,
      tn: cuts ? tn.trim() : null,
      size: cuts ? size : null,
      life: punchByHeight ? null : life.trim() || null,
      altura: punchByHeight ? altura.trim() : null,
      stripper: punchByHeight && stripper.trim() ? stripper.trim() : null,
      clearance: category === "matriz" ? clearance : null,
      so: so.trim() || null,
      proveedor: supplier.trim(),
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
            <select value={shownType} onChange={(event) => setType(event.target.value)} disabled={readOnly || !!catalogType}>
              <option value="">Select…</option>
              {shownType && !types.includes(shownType) && <option value={shownType}>{shownType} (current)</option>}
              {types.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            {catalogType && <span className="muted small">Set by the catalog for TN {tn.trim()} size {size}. Fix it in Setup &gt; Catalog if it is wrong.</span>}
          </label>
          {category === "punzon" && (
            <>
              <label>
                Total height of the tip (in)
                <input inputMode="decimal" value={altura} onChange={(event) => setAltura(event.target.value)} disabled={readOnly} placeholder="Measured, e.g. 1.000" />
              </label>
              <label>
                Stripper plate (in)
                <input inputMode="decimal" value={stripper} onChange={(event) => setStripper(event.target.value)} disabled={readOnly} placeholder={defaultStripper ? `Size ${size}: ${defaultStripper}` : "Size default"} />
                <span className="muted small">Leave empty to use the size default; type a value if this tool is different.</span>
              </label>
              {legacyLife && altura.trim() === "" && (
                <label>
                  Life (recorded before)
                  <input inputMode="decimal" value={life} onChange={(event) => setLife(event.target.value)} disabled={readOnly} />
                </label>
              )}
              <div>
                <div className="muted small">Usable life (height − stripper)</div>
                <strong>{usableLife !== null ? usableLife.toFixed(3) : (tool?.life ?? "—")}</strong>
              </div>
            </>
          )}
          {category === "matriz" && (
            <label>
              Height (in)
              <input inputMode="decimal" value={life} onChange={(event) => setLife(event.target.value)} disabled={readOnly} placeholder="e.g. 1.200" />
            </label>
          )}
          {!cuts && (
            <label>
              Life (optional)
              <input inputMode="decimal" value={life} onChange={(event) => setLife(event.target.value)} disabled={readOnly} />
            </label>
          )}
          {checkedLife !== null && Number.isFinite(checkedLife) && checkedLife <= minimum && cuts && (
            <p className="note note-warn">
              {category === "punzon" ? `The tip is at the minimum (${minimum.toFixed(3)} in. beyond the stripper).` : `The die is at or under the minimum height (${minimum.toFixed(3)} in).`} It can still be used, but it will show an alert and appear in the next purchase analysis.
            </p>
          )}
          {category === "matriz" && (
            <label>
              Clearance
              <select value={clearance} onChange={(event) => setClearance(event.target.value)} disabled={readOnly}>
                <option value="">Select…</option>
                {clearance && !(options.claridades ?? []).includes(clearance) && <option value={clearance}>{clearance} (current)</option>}
                {(options.claridades ?? []).map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            SO
            <input value={so} onChange={(event) => setSo(event.target.value)} disabled={readOnly} />
          </label>
          <label>
            Supplier
            <input list="inv-suppliers" value={supplier} onChange={(event) => setSupplier(event.target.value)} disabled={readOnly} placeholder="Pick or type a new one" autoCapitalize="characters" />
            <datalist id="inv-suppliers">
              {(options.proveedores ?? []).map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
          </label>
          <label>
            Rack
            <select value={place} onChange={(event) => setPlace(event.target.value)} disabled={readOnly}>
              <option value="">No rack assigned</option>
              {place && !racks.some((l) => l.codigo === place) && <option value={place}>{place} (current)</option>}
              {racks.map((l) => (
                <option key={l.codigo} value={l.codigo}>
                  {l.codigo}
                </option>
              ))}
            </select>
          </label>
        </div>
        <ErrorNote message={error} />
        <div className="form-actions spread">
          <span />
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
    </Dialog>
  );
}
