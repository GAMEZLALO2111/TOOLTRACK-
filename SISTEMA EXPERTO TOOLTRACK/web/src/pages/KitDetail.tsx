import { useCallback, useState } from "react";
import { api, ApiError } from "../api";
import { Chip } from "../components/MachineCard";
import { ErrorNote } from "../components/Dialog";
import { ACTION_LABEL, CATEGORY_LABEL, COMPONENT_STATE_LABEL, KIT_STATE_CLASS, KIT_STATE_LABEL } from "../labels";
import { go } from "../router";
import type { Kit } from "../types";
import { usePoll } from "../usePoll";

export function KitDetailPage({ kitId }: { kitId: string }) {
  const [kit, setKit] = useState<Kit | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setKit(await api<Kit>("GET", `/workflow/kits/${encodeURIComponent(kitId)}`));
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load the kit.");
    }
  }, [kitId]);
  usePoll(load, 15000);

  const needed = kit?.components.filter((c) => c.action !== "keep") ?? [];
  const kept = kit?.components.filter((c) => c.action === "keep") ?? [];
  const done = needed.filter((c) => c.state !== "missing").length;

  return (
    <section className="page">
      <div className="board-bar">
        <h1>{kit ? `Kit for ${kit.machine}` : "Kit"}</h1>
        <div className="bar-right">
          <button className="btn" onClick={() => go("/kitting")}>
            ← Kitting
          </button>
          <button className="btn" onClick={() => void load()}>
            Refresh
          </button>
        </div>
      </div>
      <ErrorNote message={error} />
      {kit && (
        <>
          <div className="panel kit-head">
            <div>
              <div className="muted small">Cart</div>
              <strong>{kit.cart}</strong>
            </div>
            <div>
              <div className="muted small">Status</div>
              <Chip text={KIT_STATE_LABEL[kit.state]} className={KIT_STATE_CLASS[kit.state]} />
            </div>
            <div>
              <div className="muted small">Progress</div>
              <strong>
                {done}/{needed.length}
              </strong>
            </div>
            <div>
              <div className="muted small">Created</div>
              {new Date(kit.created_at + "Z").toLocaleString()}
            </div>
            {kit.reason && (
              <div>
                <div className="muted small">Reason</div>
                {kit.reason}
              </div>
            )}
          </div>
          <div className="note note-info">
            Scanning tools, pausing, cancelling and delivery move to the web in the next step. For now continue this kit in <strong>Tool Dispensing</strong> in the desktop app.
          </div>
          <h2 className="section-title">Tools to pick ({needed.length})</h2>
          <ComponentTable rows={needed} />
          {kept.length > 0 && (
            <details className="kept">
              <summary>Tools that stay on the machine ({kept.length})</summary>
              <ComponentTable rows={kept} />
            </details>
          )}
        </>
      )}
      {!kit && !error && <p className="muted">Loading…</p>}
    </section>
  );
}

function ComponentTable({ rows }: { rows: Kit["components"] }) {
  if (rows.length === 0) return <p className="muted">Nothing to pick for this change.</p>;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Station</th>
            <th>TN</th>
            <th>Angle</th>
            <th>Part</th>
            <th>Action</th>
            <th>Tool</th>
            <th className="hide-sm">From</th>
            <th>State</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.component_id}>
              <td>{c.station ?? "—"}</td>
              <td>
                <strong>{c.tn}</strong>
              </td>
              <td>{c.angle ?? "—"}</td>
              <td>{CATEGORY_LABEL[c.category] ?? c.category}</td>
              <td>{ACTION_LABEL[c.action] ?? c.action}</td>
              <td>{c.tool_id ?? "—"}</td>
              <td className="hide-sm muted">{c.source_location ?? "—"}</td>
              <td>
                <Chip text={COMPONENT_STATE_LABEL[c.state] ?? c.state} className={c.state === "ready" ? "st-onmachine" : c.state === "cancelled" ? "st-notstarted" : "st-kitting"} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
