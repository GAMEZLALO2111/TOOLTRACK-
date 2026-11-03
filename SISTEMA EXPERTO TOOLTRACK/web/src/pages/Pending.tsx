import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { ErrorNote } from "../components/Dialog";
import { Chip } from "../components/MachineCard";
import { CATEGORY_LABEL } from "../labels";
import { go } from "../router";
import type { LocationRow, Pending } from "../types";
import { usePoll } from "../usePoll";

const DESTINATION_TYPES = ["rack", "togu", "maintenance"];

export function PendingPage() {
  const { can } = useAuth();
  const canMove = can("tools.move");
  const [rows, setRows] = useState<Pending[]>([]);
  const [destinations, setDestinations] = useState<LocationRow[]>([]);
  const [choice, setChoice] = useState<Record<number, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await api<Pending[]>("GET", "/workflow/pending-pickups"));
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load the pending pickups.");
    }
  }, []);
  usePoll(load, 20000);

  useEffect(() => {
    api<LocationRow[]>("GET", "/workflow/locations")
      .then((all) => setDestinations(all.filter((l) => l.activa && DESTINATION_TYPES.includes(l.tipo)).sort((a, b) => a.codigo.localeCompare(b.codigo))))
      .catch(() => setDestinations([]));
  }, []);

  async function move(row: Pending) {
    const destination = choice[row.pending_id] ?? row.home_rack ?? "";
    if (!destination) return setError("Choose where the tool is being stored.");
    setBusyId(row.pending_id);
    setError(null);
    setMessage(null);
    try {
      await api("POST", `/workflow/pending-pickups/${row.pending_id}/move`, { destino_codigo: destination, realizado_por: "web" });
      setMessage(`${row.tool_id} stored in ${destination}.`);
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The movement could not be saved.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="page">
      <div className="board-bar">
        <h1>Pending pickups</h1>
        <div className="bar-right">
          <button className="btn" onClick={() => go("/kitting")}>
            ← Kitting
          </button>
          <button className="btn" onClick={() => void load()}>
            Refresh
          </button>
        </div>
      </div>
      <p className="muted">Tools that came off a machine during a changeover. Record where each one is stored after you physically pick it up.</p>
      <ErrorNote message={error} />
      {message && <div className="note note-info">✔ {message}</div>}
      {rows.length === 0 && !error && <p className="note note-info">Nothing is waiting to be picked up.</p>}
      {rows.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Tool</th>
                <th>TN</th>
                <th>Part</th>
                <th>Machine</th>
                <th className="hide-sm">Setup</th>
                <th>Waiting</th>
                <th>Store in</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.pending_id} className={row.overdue ? "row-overdue" : ""}>
                  <td>
                    <strong>{row.tool_id}</strong>
                  </td>
                  <td>{row.tn}</td>
                  <td>{CATEGORY_LABEL[row.category] ?? row.category}</td>
                  <td>{row.machine}</td>
                  <td className="hide-sm muted">{row.drawing_name ?? "—"}</td>
                  <td>
                    {row.age_hours} h {row.overdue && <Chip text="OVERDUE" className="mk-urgent" />}
                  </td>
                  <td>
                    {canMove ? (
                      <select value={choice[row.pending_id] ?? row.home_rack ?? ""} onChange={(event) => setChoice({ ...choice, [row.pending_id]: event.target.value })} aria-label={`Destination for ${row.tool_id}`}>
                        <option value="">Select…</option>
                        {destinations.map((d) => (
                          <option key={d.codigo} value={d.codigo}>
                            {d.codigo} ({d.tipo})
                          </option>
                        ))}
                      </select>
                    ) : (
                      (row.home_rack ?? "—")
                    )}
                  </td>
                  <td className="row-actions">
                    {canMove && (
                      <button className="btn btn-primary" disabled={busyId === row.pending_id} onClick={() => void move(row)}>
                        Picked up and stored
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
