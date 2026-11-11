import { useAuth } from "../auth";
import { go } from "../router";
import { ReturnsTab, StoreTab } from "./ToolTrackingActions";
import { ToolFilePage } from "./ToolFile";
import { ToolsList } from "./Tools";

/** Tools: one list of every physical tool, the tool file behind each row, and the scan-and-store shortcuts. */
export function ToolsPage({ sub }: { sub: string }) {
  const { can } = useAuth();

  if (sub === "store" || sub === "returns") {
    const allowed = sub === "store" ? can("tools.store") : can("kitting.view");
    return (
      <section className="page">
        <div className="board-bar">
          <h1>{sub === "store" ? "Store in rack" : "Machine returns"}</h1>
          <div className="bar-right">
            <button className="btn" onClick={() => go("/tools")}>
              ← Tools
            </button>
          </div>
        </div>
        {!allowed ? <p className="note note-info">Your role cannot use this.</p> : sub === "store" ? <StoreTab /> : <ReturnsTab />}
      </section>
    );
  }
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
            <>
              <button className="btn" onClick={() => go("/tools/returns")}>
                Machine returns
              </button>
              <button className="btn" onClick={() => go("/kitting/pending")}>
                Pending pickups
              </button>
            </>
          )}
        </div>
      </div>
      <ToolsList />
    </section>
  );
}
