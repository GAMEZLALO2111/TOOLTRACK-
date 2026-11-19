import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { Dialog, ErrorNote } from "./Dialog";
import type { ToolParameters } from "../types";

const LABELS: Record<string, string> = {
  punch_min_life: "Punch: minimum tip beyond the stripper (in)",
  die_min_height: "Die: minimum height (in)",
  tn_tolerance: "Size tolerance when kitting (± in)",
};

export function LifeSettingsDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [data, setData] = useState<ToolParameters | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<ToolParameters>("GET", "/inventario/parametros")
      .then((loaded) => {
        setData(loaded);
        setValues(loaded.valores);
      })
      .catch((caught) => setError(caught instanceof ApiError ? caught.message : "Could not load the settings."));
  }, []);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api("PUT", "/inventario/parametros", { valores: values, realizado_por: "web" });
      onSaved();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The settings could not be saved.");
      setBusy(false);
    }
  }

  const row = (key: string, label: string) => (
    <label key={key}>
      {label}
      <input inputMode="decimal" value={values[key] ?? ""} onChange={(event) => setValues({ ...values, [key]: event.target.value })} />
      {data && Number(values[key]) !== Number(data.por_defecto[key]) && <span className="muted small">Default: {data.por_defecto[key]}</span>}
    </label>
  );

  return (
    <Dialog
      title="Life settings"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={() => void save()} disabled={busy || !data}>
            {busy ? "Saving…" : "Save"}
          </button>
        </>
      }
    >
      {!data && !error && <p className="muted">Loading…</p>}
      {data && (
        <div className="form">
          <p className="muted small">
            A punch&apos;s usable life is the measured height of its tip minus the stripper plate of its size. Each tool can still have its own stripper value when it is registered.
          </p>
          <h3>Stripper plate by size (in)</h3>
          <div className="two-col">{data.tamanos.map((size) => row(`stripper_${size}`, `Size ${size}`))}</div>
          <h3>Minimums and tolerance</h3>
          <div className="two-col">{Object.entries(LABELS).map(([key, label]) => row(key, label))}</div>
        </div>
      )}
      <ErrorNote message={error} />
    </Dialog>
  );
}
