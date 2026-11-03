import { useAuth } from "../auth";
import { go } from "../router";
import { ReturnsTab, ServiceTab, StoreTab } from "./ToolTrackingActions";
import { FindTab } from "./ToolTrackingFind";

const TABS = [
  { key: "find", label: "Find a tool", permission: "inventory.view" },
  { key: "store", label: "Store in rack", permission: "tools.store" },
  { key: "returns", label: "Machine returns", permission: "kitting.view" },
  { key: "service", label: "In service", permission: "inventory.view" },
];

export function ToolTrackingPage({ tab }: { tab: string }) {
  const { can } = useAuth();
  const allowed = TABS.filter((t) => can(t.permission));
  const current = allowed.find((t) => t.key === tab) ?? allowed[0];
  if (!current) return <p className="note note-info">Your role cannot use Tool Tracking.</p>;

  return (
    <section className="page">
      <div className="board-bar">
        <h1>Tool Tracking</h1>
        <div className="bar-right">
          {can("kitting.view") && (
            <button className="btn" onClick={() => go("/kitting/pending")}>
              Pending pickups
            </button>
          )}
        </div>
      </div>
      <div className="tabs-inline wrap">
        {allowed.map((t) => (
          <button key={t.key} className={`btn${t.key === current.key ? " btn-primary" : ""}`} onClick={() => go(`/tracking/${t.key}`)}>
            {t.label}
          </button>
        ))}
      </div>
      {current.key === "find" && <FindTab />}
      {current.key === "store" && <StoreTab />}
      {current.key === "returns" && <ReturnsTab />}
      {current.key === "service" && <ServiceTab />}
    </section>
  );
}
