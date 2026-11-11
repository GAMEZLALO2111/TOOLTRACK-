import { CatalogTab } from "./InventoryCatalog";

export function CatalogPage() {
  return (
    <section className="page">
      <div className="board-bar">
        <h1>Catalog</h1>
      </div>
      <CatalogTab />
    </section>
  );
}
