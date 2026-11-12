import { useAuth } from "../auth";
import { go } from "../router";
import { LabelsPage } from "./Labels";
import { StoreTab } from "./ToolTrackingActions";
import { ToolFilePage } from "./ToolFile";
import { ToolsList } from "./Tools";

/** Tools: one list of every physical tool, the tool file behind each row, and the scan-and-store shortcut. */
export function ToolsPage({ sub }: { sub: string }) {
  const { can } = useAuth();

  if (sub === "store") {
    return (
      <section className="page">
        <div className="board-bar">
          <h1>Store in rack</h1>
          <div className="bar-right">
            <button className="btn" onClick={() => go("/tools")}>
              ← Tools
            </button>
          </div>
        </div>
        {can("tools.store") ? <StoreTab /> : <p className="note note-info">Your role cannot use this.</p>}
      </section>
    );
  }
  if (sub === "labels") return can("kitting.view") ? <LabelsPage /> : <p className="note note-info">Your role cannot use this.</p>;
  if (sub) return <ToolFilePage toolId={decodeURIComponent(sub)} />;

  return (
    <section className="page">
      <div className="board-bar">
        <h1>Tools</h1>
        <div className="bar-right">
          {can("tools.store") && (
            <button className="btn btn-primary" onClick={() => go("/tools/store")}>
              Store in rack
            </button>
          )}
          {can("kitting.view") && (
            <button className="btn" onClick={() => go("/tools/labels")}>
              QR labels
            </button>
          )}
        </div>
      </div>
      <ToolsList />
    </section>
  );
}
