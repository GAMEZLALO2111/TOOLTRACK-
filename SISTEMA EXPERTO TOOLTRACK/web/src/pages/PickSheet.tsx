import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { ErrorNote } from "../components/Dialog";
import { CATEGORY_LABEL } from "../labels";
import { go } from "../router";
import type { Candidate, PickSheet } from "../types";

function toolText(tool: Candidate) {
  return [tool.type, tool.size].filter(Boolean).join(" ");
}

export function PickSheetPage({ kitId }: { kitId: string }) {
  const [sheet, setSheet] = useState<PickSheet | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<PickSheet>("GET", `/workflow/kits/${encodeURIComponent(kitId)}/pick-sheet`)
      .then(setSheet)
      .catch((reason) => setError(reason instanceof ApiError ? reason.message : "Could not load the pick sheet"));
  }, [kitId]);

  return (
    <section className="page">
      <div className="board-bar no-print">
        <h1>Pick sheet</h1>
        <div className="bar-right">
          <button className="btn" onClick={() => go(`/kitting/kit/${encodeURIComponent(kitId)}`)}>
            ← Kit
          </button>
          <button className="btn btn-primary" disabled={!sheet} onClick={() => window.print()}>
            Print
          </button>
        </div>
      </div>
      <ErrorNote message={error} />
      {!sheet && !error && <p className="muted">Loading…</p>}
      {sheet && (
        <div className="print-area pick-sheet">
          <div className="pick-head">
            <h2>
              {sheet.machine} · {sheet.part_number ?? "—"}
            </h2>
            <div className="pick-facts">
              <span>Thickness <strong>{sheet.thickness ? `${sheet.thickness} in` : "—"}</strong></span>
              <span>
                Die clearance{" "}
                <strong>{sheet.clearance_min && sheet.clearance_max ? `${sheet.clearance_min} – ${sheet.clearance_max}` : "—"}</strong>
              </span>
              <span>Cart <strong>{sheet.cart}</strong></span>
              <span>Printed <strong>{new Date().toLocaleString()}</strong></span>
            </div>
          </div>
          {sheet.items.length === 0 ? (
            <p>Nothing left to pick for this kit.</p>
          ) : (
            <table className="table pick-table">
              <thead>
                <tr>
                  <th></th>
                  <th>Rack</th>
                  <th>Pick</th>
                  <th>Tool</th>
                  <th>Station</th>
                  <th>Need</th>
                  <th>If missing</th>
                  <th>Scanned ID</th>
                </tr>
              </thead>
              <tbody>
                {sheet.items.map((item, index) => (
                  <tr key={index}>
                    <td className="pick-box">☐</td>
                    <td>
                      <strong>{item.suggested?.location ?? "—"}</strong>
                    </td>
                    <td>
                      {item.suggested ? (
                        <>
                          <strong>{item.suggested.id}</strong>
                          <div className="muted">{toolText(item.suggested)}</div>
                          {item.suggested.substitute && (
                            <div className="muted">TN {item.suggested.tn}{item.suggested.tn_difference ? ` (${item.suggested.tn_difference} in.)` : ""}</div>
                          )}
                          {item.suggested.life_alert && <div><strong>At minimum life</strong></div>}
                        </>
                      ) : (
                        <strong>No tool in a rack</strong>
                      )}
                    </td>
                    <td>
                      {CATEGORY_LABEL[item.category] ?? item.category} · TN {item.tn}
                    </td>
                    <td>{[item.station, item.angle && `${item.angle}°`].filter(Boolean).join(" · ") || "—"}</td>
                    <td>
                      {item.category === "matriz" && item.clearance_min && item.clearance_max
                        ? `Clearance ${item.clearance_min} – ${item.clearance_max}`
                        : "—"}
                    </td>
                    <td>
                      {item.alternates.length === 0
                        ? "—"
                        : item.alternates.map((tool) => (
                            <div key={tool.id}>
                              <strong>{tool.id}</strong> · {tool.location}
                              {item.category === "matriz" && tool.clearance ? ` · ${tool.clearance}` : ""}
                            </div>
                          ))}
                    </td>
                    <td className="pick-write"></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  );
}
