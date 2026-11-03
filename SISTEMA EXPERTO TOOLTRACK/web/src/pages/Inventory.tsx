import { useAuth } from "../auth";
import { go } from "../router";
import { CatalogTab } from "./InventoryCatalog";
import { ToolsTab } from "./InventoryTools";

const TABS = [
  { key: "tools", label: "Physical tools", permission: "inventory.view" },
  { key: "catalog", label: "Catalog (TN, sizes, variants)", permission: "catalog.view" },
];

export function InventoryPage({ tab }: { tab: string }) {
  const { can } = useAuth();
  const allowed = TABS.filter((t) => can(t.permission));
  const current = allowed.find((t) => t.key === tab) ?? allowed[0];
  if (!current) return <p className="note note-info">Your role cannot use Inventory.</p>;
  return (
    <section className="page">
      <div className="board-bar">
        <h1>Inventory</h1>
      </div>
      <div className="tabs-inline wrap">
        {allowed.map((t) => (
          <button key={t.key} className={`btn${t.key === current.key ? " btn-primary" : ""}`} onClick={() => go(`/inventory/${t.key}`)}>
            {t.label}
          </button>
        ))}
      </div>
      {current.key === "tools" && <ToolsTab />}
      {current.key === "catalog" && <CatalogTab />}
    </section>
  );
}
