import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { ConfirmDialog, Dialog, ErrorNote } from "../components/Dialog";
import { Chip } from "../components/MachineCard";
import { CATEGORY_LABEL, movementLabel, TOOL_STATE_CLASS } from "../labels";
import type { Movement, ToolRow } from "../types";

export function FindTab() {
  const { can } = useAuth();
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<ToolRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<ToolRow | null>(null);

  const search = useCallback(async (text: string) => {
    setBusy(true);
    setError(null);
    try {
      setRows(await api<ToolRow[]>("GET", `/workflow/tools/locate?q=${encodeURIComponent(text.trim())}&limit=300`));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The search failed.");
    } finally {
      setBusy(false);
    }
  }, []);

  return (
    <div>
      <form
        className="start-row"
        onSubmit={(event) => {
          event.preventDefault();
          void search(query);
        }}
      >
        <input className="grow" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tool ID or Tool Number (TN)" autoFocus aria-label="Search a tool" />
        <button className="btn btn-primary" type="submit" disabled={busy}>
          Search
        </button>
        <button className="btn" type="button" disabled={busy} onClick={() => void search("")}>
          Show first 300
        </button>
      </form>
      <ErrorNote message={error} />
      {rows && rows.length === 0 && <p className="note note-info">No tools match “{query}”.</p>}
      {rows && rows.length > 0 && (
        <>
          <p className="muted small">{rows.length} tool(s){rows.length >= 300 ? " (showing the first 300; narrow the search)" : ""}. Click a row for its history.</p>
          <div className="table-wrap">
            <table className="table clickable-rows">
              <thead>
                <tr>
                  <th>Tool</th>
                  <th>TN</th>
                  <th>Part</th>
                  <th>State</th>
                  <th>Where it is</th>
                  <th className="hide-sm">Home rack</th>
                  <th className="hide-sm">Part number</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.tool_id} className={row.estado === "baja" ? "inactive" : ""} onClick={() => setSelected(row)} tabIndex={0} onKeyDown={(event) => event.key === "Enter" && setSelected(row)}>
                    <td>
                      <strong>{row.tool_id}</strong>
                    </td>
                    <td>{row.tn}</td>
                    <td>{CATEGORY_LABEL[row.category ?? ""] ?? row.category ?? "—"}</td>
                    <td>
                      <Chip text={row.estado_label} className={TOOL_STATE_CLASS[row.estado] ?? ""} />
                    </td>
                    <td className={row.pending_pickup ? "warn-text" : ""}>{row.status}</td>
                    <td className="hide-sm">{row.home_rack ?? "—"}</td>
                    <td className="hide-sm muted">{row.drawing_name ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {selected && (
        <ToolDialog
          tool={selected}
          canChangeStatus={can("tools.status")}
          onClose={() => setSelected(null)}
          onChanged={() => {
            setSelected(null);
            void search(query);
          }}
        />
      )}
    </div>
  );
}

function ToolDialog({ tool, canChangeStatus, onClose, onChanged }: { tool: ToolRow; canChangeStatus: boolean; onClose: () => void; onChanged: () => void }) {
  const [history, setHistory] = useState<Movement[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmRetire, setConfirmRetire] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Movement[]>("GET", `/workflow/movements?tool_id=${encodeURIComponent(tool.tool_id)}`)
      .then(setHistory)
      .catch((caught) => setError(caught instanceof ApiError ? caught.message : "Could not load the history."));
  }, [tool.tool_id]);

  async function change(estado: "danado" | "disponible" | "baja") {
    setBusy(true);
    setError(null);
    try {
      await api("POST", `/workflow/tools/${encodeURIComponent(tool.tool_id)}/status`, { estado, realizado_por: "web" });
      onChanged();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The change could not be saved.");
      setBusy(false);
    }
  }

  const retired = tool.estado === "baja";
  return (
    <Dialog title={`${tool.tool_id} · TN ${tool.tn}`} onClose={onClose} wide>
      <div className="facts">
        <Fact label="State" value={<Chip text={tool.estado_label} className={TOOL_STATE_CLASS[tool.estado] ?? ""} />} />
        <Fact label="Where" value={tool.status} />
        <Fact label="Part" value={`${CATEGORY_LABEL[tool.category ?? ""] ?? tool.category ?? "—"}${tool.type ? ` · ${tool.type}` : ""}${tool.size ? ` · size ${tool.size}` : ""}`} />
        <Fact label="Clearance" value={tool.clearance ?? "—"} />
        <Fact label="Home rack" value={tool.home_rack ?? "—"} />
        {tool.drawing_name && <Fact label="Part number" value={`${tool.drawing_name}${tool.station ? ` · station ${tool.station}` : ""}`} />}
      </div>
      {canChangeStatus && !retired && (
        <div className="kit-actions">
          {tool.estado !== "danado" && (
            <button className="btn" disabled={busy} onClick={() => void change("danado")}>
              Mark as damaged
            </button>
          )}
          {tool.estado !== "disponible" && (
            <button className="btn" disabled={busy} onClick={() => void change("disponible")}>
              Release (available)
            </button>
          )}
          <button className="btn btn-ghost-danger" disabled={busy} onClick={() => setConfirmRetire(true)}>
            Retire
          </button>
        </div>
      )}
      <ErrorNote message={error} />
      <h3 className="q-done-title">History</h3>
      {!history && !error && <p className="muted">Loading…</p>}
      {history && history.length === 0 && <p className="muted">No movements recorded.</p>}
      {history && history.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>When</th>
                <th>Movement</th>
                <th>From → To</th>
                <th>By</th>
                <th className="hide-sm">Details</th>
              </tr>
            </thead>
            <tbody>
              {history.map((movement) => (
                <tr key={movement.id}>
                  <td>{new Date(movement.created_at + "Z").toLocaleString()}</td>
                  <td>{movementLabel(movement.type)}</td>
                  <td>
                    {movement.from ?? "—"} → {movement.to ?? "—"}
                  </td>
                  <td>{movement.by}</td>
                  <td className="hide-sm muted">{movement.details ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {confirmRetire && (
        <ConfirmDialog
          title="Retire tool"
          message={`Retire ${tool.tool_id}? It can never be kitted again.`}
          confirmLabel="Retire"
          danger
          onClose={() => setConfirmRetire(false)}
          onConfirm={() => {
            setConfirmRetire(false);
            void change("baja");
          }}
        />
      )}
    </Dialog>
  );
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="muted small">{label}</div>
      <div>{value}</div>
    </div>
  );
}
