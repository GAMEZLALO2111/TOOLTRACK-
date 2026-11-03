import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { ErrorNote } from "../components/Dialog";
import { Qr } from "../components/Qr";
import { go } from "../router";
import type { LocationRow } from "../types";

type Kind = "machine" | "cart";

const PREFIX: Record<Kind, string> = { machine: "TOOLTRACK-MACHINE:", cart: "TOOLTRACK-CART:" };
const TITLE: Record<Kind, string> = { machine: "Machine labels", cart: "Cart labels" };

export function LabelsPage() {
  const [locations, setLocations] = useState<LocationRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState<Kind>("machine");
  const [picked, setPicked] = useState<Set<string>>(new Set());

  useEffect(() => {
    api<LocationRow[]>("GET", "/workflow/locations")
      .then((all) => setLocations(all.filter((l) => l.activa)))
      .catch((caught) => setError(caught instanceof ApiError ? caught.message : "Could not load the locations."));
  }, []);

  const items = locations.filter((l) => l.tipo === kind).sort((a, b) => a.codigo.localeCompare(b.codigo));
  const shown = items.filter((l) => picked.size === 0 || picked.has(l.codigo));

  function toggle(code: string) {
    const next = new Set(picked);
    if (next.has(code)) next.delete(code);
    else next.add(code);
    setPicked(next);
  }

  return (
    <section className="page">
      <div className="board-bar no-print">
        <h1>QR labels</h1>
        <div className="bar-right">
          <button className="btn" onClick={() => go("/kitting")}>
            ← Kitting
          </button>
          <button className="btn btn-primary" onClick={() => window.print()} disabled={shown.length === 0}>
            Print {shown.length} label{shown.length === 1 ? "" : "s"}
          </button>
        </div>
      </div>
      <div className="no-print">
        <div className="tabs-inline">
          {(["machine", "cart"] as Kind[]).map((k) => (
            <button key={k} className={`btn${kind === k ? " btn-primary" : ""}`} onClick={() => { setKind(k); setPicked(new Set()); }}>
              {k === "machine" ? "Machines" : "Carts"}
            </button>
          ))}
        </div>
        <p className="muted small">
          Print one per {kind} and stick it where it is scanned. Machine labels are scanned at the machine to confirm a delivery.
          {kind === "cart" ? " Cart labels are not read by the system yet; they identify the cart physically." : ""} Select some to print only those; with none selected, all are printed.
        </p>
        <ErrorNote message={error} />
        <div className="chips pick-chips">
          {items.map((l) => (
            <label key={l.codigo} className="check">
              <input type="checkbox" checked={picked.has(l.codigo)} onChange={() => toggle(l.codigo)} /> {l.codigo}
            </label>
          ))}
        </div>
      </div>

      <div className="print-area label-sheet" aria-label={TITLE[kind]}>
        {shown.map((l) => (
          <div key={l.codigo} className="label">
            <Qr value={`${PREFIX[kind]}${l.codigo}`} size={150} />
            <div className="label-title">{l.codigo}</div>
            <div className="label-sub">{l.nombre !== l.codigo ? l.nombre : kind === "machine" ? "Machine" : "Cart"}</div>
            <div className="label-code">{PREFIX[kind]}{l.codigo}</div>
          </div>
        ))}
      </div>
      {shown.length === 0 && !error && <p className="muted no-print">No active {kind === "machine" ? "machines" : "carts"} registered.</p>}
    </section>
  );
}
