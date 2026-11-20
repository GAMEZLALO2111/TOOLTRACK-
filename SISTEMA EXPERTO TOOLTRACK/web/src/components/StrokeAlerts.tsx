import { useCallback, useState } from "react";
import { api } from "../api";
import { usePoll } from "../usePoll";
import type { StrokeAlert } from "../types";
import { MachineLink, ToolLink } from "./Links";

export function useStrokeAlerts(): StrokeAlert[] {
  const [rows, setRows] = useState<StrokeAlert[]>([]);
  const load = useCallback(async () => {
    try {
      setRows(await api<StrokeAlert[]>("GET", "/workflow/strokes/alerts"));
    } catch {
      setRows([]);
    }
  }, []);
  usePoll(load, 60000);
  return rows;
}

export function StrokeAlertTable({ rows }: { rows: StrokeAlert[] }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Tool</th>
            <th>TN</th>
            <th>Where</th>
            <th>Strokes</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.tool_id}>
              <td>
                <ToolLink id={row.tool_id} />
                <div className="muted small">{row.category === "punzon" ? "Punch" : "Die"}</div>
              </td>
              <td>{row.tn ?? "—"}</td>
              <td>
                {row.machine ? (
                  <>
                    <MachineLink code={row.machine} />
                    <div className="muted small">station {row.station}</div>
                  </>
                ) : (
                  row.location ?? "—"
                )}
              </td>
              <td>
                {row.used.toLocaleString()} / {row.limit?.toLocaleString()}
                <div className="muted small">{row.stage === "first" ? "first maintenance" : "sharpening interval"} · {row.pct}%</div>
              </td>
              <td>
                {row.level === "due" ? <span className="chip st-missing">Limit reached</span> : <span className="chip st-kitting">Near the limit</span>}
                {row.request && <div className="muted small">{row.request}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
