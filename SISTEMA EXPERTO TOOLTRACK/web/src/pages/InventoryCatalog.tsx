import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { Dialog, ErrorNote } from "../components/Dialog";
import { Chip } from "../components/MachineCard";
import type { CatalogConfig, InvOptions } from "../types";

type Dialogs = { kind: "tn" } | { kind: "config" } | { kind: "variant"; config: CatalogConfig } | null;

export function CatalogTab() {
  const { can } = useAuth();
  const canEdit = can("catalog.edit");
  const [configs, setConfigs] = useState<CatalogConfig[]>([]);
  const [tns, setTns] = useState<string[]>([]);
  const [options, setOptions] = useState<InvOptions | null>(null);
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialogs>(null);

  const load = useCallback(async () => {
    try {
      const [c, t] = await Promise.all([api<CatalogConfig[]>("GET", "/catalogo/configuraciones"), api<{ tn: string }[]>("GET", "/catalogo/tn")]);
      setConfigs(c);
      setTns(t.map((row) => row.tn));
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load the catalog.");
    }
  }, []);

  useEffect(() => {
    void load();
    api<InvOptions>("GET", "/inventario/opciones").then(setOptions).catch(() => setOptions(null));
  }, [load]);

  const shown = useMemo(() => {
    const text = search.trim().toLowerCase();
    return text ? configs.filter((c) => c.tn.toLowerCase().includes(text) || c.type.toLowerCase().includes(text)) : configs;
  }, [configs, search]);
  const withoutConfig = useMemo(() => tns.filter((tn) => !configs.some((c) => c.tn === tn)), [tns, configs]);

  return (
    <div>
      <p className="muted">
        The catalog says which tool numbers exist: each TN has one or more sizes (a geometry type), and each size has punch and die variants (a die per clearance). Physical tools link to a variant.
      </p>
      <div className="summary">
        <span className="chip sum lvl-VERDE">{tns.length} tool numbers</span>
        <span className="chip sum lvl-PARO">{configs.length} TN/size configurations</span>
        {canEdit && (
          <span className="push-right pair">
            <button className="btn" onClick={() => setDialog({ kind: "tn" })}>
              Add tool number
            </button>
            <button className="btn" onClick={() => setDialog({ kind: "config" })} disabled={tns.length === 0}>
              Add configuration
            </button>
          </span>
        )}
      </div>
      <div className="filters">
        <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search TN or type" aria-label="Search the catalog" />
      </div>
      <ErrorNote message={error} />
      {withoutConfig.length > 0 && (
        <div className="note note-warn">
          {withoutConfig.length} tool number(s) have no size configuration yet: {withoutConfig.slice(0, 8).join(", ")}
          {withoutConfig.length > 8 ? "…" : ""}
        </div>
      )}
      {shown.length === 0 && !error && <p className="note note-info">Nothing matches.</p>}
      {shown.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>TN</th>
                <th>Size</th>
                <th>Type</th>
                <th>Variants</th>
                {canEdit && <th />}
              </tr>
            </thead>
            <tbody>
              {shown.map((config) => (
                <tr key={`${config.tn}/${config.size}`}>
                  <td>
                    <strong>{config.tn}</strong>
                  </td>
                  <td>{config.size}</td>
                  <td>{config.type}</td>
                  <td>
                    <div className="chips">
                      {config.variantes.length === 0 && <span className="muted">None</span>}
                      {config.variantes.map((v) => (
                        <Chip key={v.id} text={v.categoria === "punzon" ? "Punch" : `Die ${v.clearance}`} className={v.categoria === "punzon" ? "st-onmachine" : "st-kitting"} />
                      ))}
                    </div>
                  </td>
                  {canEdit && (
                    <td className="row-actions">
                      <button className="btn" onClick={() => setDialog({ kind: "variant", config })}>
                        Add variant
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {dialog?.kind === "tn" && <TnForm onClose={() => setDialog(null)} onSaved={() => { setDialog(null); void load(); }} />}
      {dialog?.kind === "config" && options && <ConfigForm tns={tns} options={options} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); void load(); }} />}
      {dialog?.kind === "variant" && <VariantForm config={dialog.config} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); void load(); }} />}
    </div>
  );
}

function useSave(onSaved: () => void) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      onSaved();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "It could not be saved.");
      setBusy(false);
    }
  }
  return { error, setError, busy, run };
}

function Actions({ onClose, busy }: { onClose: () => void; busy: boolean }) {
  return (
    <div className="form-actions">
      <button type="button" className="btn" onClick={onClose}>
        Cancel
      </button>
      <button type="submit" className="btn btn-primary" disabled={busy}>
        Save
      </button>
    </div>
  );
}

function TnForm({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [tn, setTn] = useState("");
  const { error, setError, busy, run } = useSave(onSaved);
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!tn.trim()) return setError("Enter the tool number.");
    void run(() => api("POST", "/catalogo/tn", { tn: tn.trim() }));
  }
  return (
    <Dialog title="Add a tool number" onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <label>
          TN
          <input value={tn} onChange={(event) => setTn(event.target.value)} autoFocus />
        </label>
        <ErrorNote message={error} />
        <Actions onClose={onClose} busy={busy} />
      </form>
    </Dialog>
  );
}

function ConfigForm({ tns, options, onClose, onSaved }: { tns: string[]; options: InvOptions; onClose: () => void; onSaved: () => void }) {
  const [tn, setTn] = useState(tns[0] ?? "");
  const [size, setSize] = useState("A");
  const [type, setType] = useState("");
  const { error, setError, busy, run } = useSave(onSaved);
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!tn || !type) return setError("Choose the TN and the type.");
    void run(() => api("POST", "/catalogo/configuraciones", { tn, size, type }));
  }
  return (
    <Dialog title="Add a TN/size configuration" onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <label>
          TN
          <select value={tn} onChange={(event) => setTn(event.target.value)}>
            {tns.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label>
          Size
          <select value={size} onChange={(event) => setSize(event.target.value)}>
            {options.sizes.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label>
          Type (geometry)
          <select value={type} onChange={(event) => setType(event.target.value)}>
            <option value="">Select…</option>
            {options.tipos.punzon.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <ErrorNote message={error} />
        <Actions onClose={onClose} busy={busy} />
      </form>
    </Dialog>
  );
}

function VariantForm({ config, onClose, onSaved }: { config: CatalogConfig; onClose: () => void; onSaved: () => void }) {
  const [category, setCategory] = useState("punzon");
  const [clearance, setClearance] = useState("");
  const { error, setError, busy, run } = useSave(onSaved);
  function submit(event: FormEvent) {
    event.preventDefault();
    if (category === "matriz" && !clearance.trim()) return setError("A die needs its clearance.");
    void run(() => api("POST", `/catalogo/configuraciones/${encodeURIComponent(config.tn)}/${encodeURIComponent(config.size)}/variantes`, { categoria: category, clearance: category === "matriz" ? clearance.trim() : null }));
  }
  return (
    <Dialog title={`Add a variant to ${config.tn} · size ${config.size}`} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <label>
          Category
          <select value={category} onChange={(event) => setCategory(event.target.value)}>
            <option value="punzon">Punch</option>
            <option value="matriz">Die</option>
          </select>
        </label>
        {category === "matriz" && (
          <label>
            Clearance (up to 3 decimals)
            <input inputMode="decimal" value={clearance} onChange={(event) => setClearance(event.target.value)} placeholder="e.g. 0.016" autoFocus />
          </label>
        )}
        <ErrorNote message={error} />
        <Actions onClose={onClose} busy={busy} />
      </form>
    </Dialog>
  );
}
