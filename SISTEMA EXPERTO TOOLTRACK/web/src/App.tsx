import { AuthProvider, useAuth } from "./auth";
import { BoardPage } from "./pages/Board";
import { KitDetailPage } from "./pages/KitDetail";
import { KittingPage } from "./pages/Kitting";
import { LoginPage } from "./pages/Login";
import { ReturnsPage } from "./pages/Returns";
import { CatalogPage } from "./pages/Catalog";
import { ProgramsPage } from "./pages/Programs";
import { ToolsPage } from "./pages/ToolsPage";
import { MachinesPage } from "./pages/Machines";
import { ManufacturingPage } from "./pages/Manufacturing";
import { MaintenancePage } from "./pages/Maintenance";
import { RecordsPage } from "./pages/Records";
import { ServiceRequestsPage } from "./pages/ServiceRequests";
import { RequestsTab } from "./components/RequestsTab";
import { UsersPage } from "./pages/Users";
import { go, useRoute } from "./router";

function Shell() {
  const { session, can, logout } = useAuth();
  const route = useRoute();

  if (!session) return <LoginPage />;
  if (route.path === "/tv" && can("demand.view")) return <BoardPage tv />;

  // Reference data lives under one menu entry, Setup, with its own tab strip.
  const setup = [
    { path: "/catalog", label: "Catalog", show: can("catalog.view") },
    { path: "/programs", label: "Part numbers", show: can("program.view") },
    { path: "/machines", label: "Machines", show: can("machine.view") },
  ].filter((item) => item.show);
  const within = (path: string, base: string) => path === base || path.startsWith(`${base}/`);
  const catalogRoute = route.path === "/catalog" || route.path === "/inventory/catalog";
  const inSetup = setup.some((item) => within(route.path, item.path)) || (catalogRoute && setup.length > 0);

  const tabs = [
    { path: "/board", label: "Board", show: can("demand.view") },
    { path: "/kitting", label: "Kitting", show: can("kitting.view") },
    { path: "/requests", label: "Requests", show: can("service.view") },
    { path: "/maintenance", label: "Maintenance", show: can("maintenance.view") },
    { path: "/tools", label: "Tools", show: can("inventory.view") },
    { path: "/records", label: "Records", show: can("records.view") },
    { path: setup[0]?.path ?? "/catalog", label: setup.length === 1 ? setup[0].label : "Setup", show: setup.length > 0, setup: true },
    { path: "/manufacturing", label: "Manufacturing", show: can("manufacturing.view") },
    { path: "/users", label: "Users", show: can("user.manage") },
  ].filter((tab) => tab.show);

  const kitMatch = route.path.match(/^\/kitting\/kit\/([^/]+)$/);
  // /tracking and /inventory are the earlier addresses of Tools and Catalog.
  const toolsMatch = route.path.match(/^\/tools(?:\/(.+))?$/);
  const oldTrackingMatch = route.path.match(/^\/tracking(?:\/([a-z]+))?$/);
  const oldInventoryMatch = route.path.match(/^\/inventory(?:\/([a-z]+))?$/);
  const programsMatch = route.path.match(/^\/programs(?:\/([a-z0-9]+))?$/);
  const machinesMatch = route.path.match(/^\/machines(?:\/([^/]+))?$/);
  const manufacturingMatch = route.path === "/manufacturing";
  const recordsMatch = route.path.match(/^\/records(?:\/([a-z]+))?$/);
  const returnsRoute = route.path === "/kitting/returns" || route.path === "/kitting/pending" || oldTrackingMatch?.[1] === "returns";
  let page;
  if (route.path === "/users" && can("user.manage")) page = <UsersPage />;
  else if (programsMatch && can("program.view")) page = <ProgramsPage tab={programsMatch[1] ?? ""} />
  else if (machinesMatch && can("machine.view")) page = <MachinesPage code={machinesMatch[1] ? decodeURIComponent(machinesMatch[1]) : null} />
  else if (manufacturingMatch && can("manufacturing.view")) page = <ManufacturingPage route={route} />
  else if (route.path === "/requests" && can("service.view")) page = <ServiceRequestsPage />
  else if (route.path === "/maintenance" && can("maintenance.view")) page = <MaintenancePage />
  else if (recordsMatch && can("records.view")) page = <RecordsPage kind={recordsMatch[1] ?? ""} />
  else if (kitMatch && can("kitting.view")) page = <KitDetailPage kitId={decodeURIComponent(kitMatch[1])} />;
  else if (returnsRoute && can("kitting.view")) page = <ReturnsPage />;
  else if (route.path === "/kitting/labels" && can("kitting.view")) page = <ToolsPage sub="labels" />;
  else if (route.path === "/kitting" && can("kitting.view")) page = <KittingPage route={route} />;
  else if (catalogRoute && can("catalog.view")) page = <CatalogPage />
  else if (toolsMatch && can("inventory.view")) page = <ToolsPage sub={toolsMatch[1] ?? ""} />
  else if (oldTrackingMatch && can("inventory.view")) page = <ToolsPage sub={oldTrackingMatch[1] === "store" ? "store" : ""} />
  else if (oldInventoryMatch && can("inventory.view")) page = <ToolsPage sub="" />
  else if (can("demand.view")) page = <BoardPage />;
  else if (can("kitting.view")) page = <KittingPage route={route} />;
  else if (can("maintenance.operate")) page = <MaintenancePage />;
  else if (can("inventory.view")) page = <ToolsPage sub="" />;
  else if (can("manufacturing.view")) page = <ManufacturingPage route={route} />;
  else page = <p className="note note-info">Your role has no modules in the web app yet. Use the desktop app for now.</p>;

  return (
    <div className="app">
      <header className="topbar">
        <nav>
          {tabs.map((tab) => {
            const current = tab.setup ? inSetup : within(route.path, tab.path);
            return tab.path === "/requests" ? (
              <RequestsTab key={tab.path} active={current} programOnly={!can("tools.move")} />
            ) : (
              <a key={tab.path} href={`#${tab.path}`} className={current ? "active" : ""}>
                {tab.label}
              </a>
            );
          })}
        </nav>
        <div className="who">
          <span>
            {session.username} · <small>{session.role}</small>
          </span>
          <button className="btn btn-light" onClick={() => void logout()}>
            Sign out
          </button>
        </div>
      </header>
      <main className="content">
        {inSetup && setup.length > 1 && (
          <div className="tabs-inline wrap">
            {setup.map((item) => (
              <button key={item.path} className={`btn${within(route.path, item.path) || (item.path === "/catalog" && catalogRoute) ? " btn-primary" : ""}`} onClick={() => go(item.path)}>
                {item.label}
              </button>
            ))}
          </div>
        )}
        {page}
      </main>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  );
}
