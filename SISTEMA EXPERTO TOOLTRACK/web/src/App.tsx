import { AuthProvider, useAuth } from "./auth";
import { BoardPage } from "./pages/Board";
import { KitDetailPage } from "./pages/KitDetail";
import { KittingPage } from "./pages/Kitting";
import { LoginPage } from "./pages/Login";
import { UsersPage } from "./pages/Users";
import { useRoute } from "./router";

function Shell() {
  const { session, can, logout } = useAuth();
  const route = useRoute();

  if (!session) return <LoginPage />;
  if (route.path === "/tv" && can("demand.view")) return <BoardPage tv />;

  const tabs = [
    { path: "/board", label: "Sequence Board", show: can("demand.view") },
    { path: "/kitting", label: "Kitting", show: can("kitting.view") },
    { path: "/users", label: "Users", show: can("user.manage") },
  ].filter((tab) => tab.show);

  const kitMatch = route.path.match(/^\/kitting\/kit\/([^/]+)$/);
  let page;
  if (route.path === "/users" && can("user.manage")) page = <UsersPage />;
  else if (kitMatch && can("kitting.view")) page = <KitDetailPage kitId={decodeURIComponent(kitMatch[1])} />;
  else if (route.path === "/kitting" && can("kitting.view")) page = <KittingPage route={route} />;
  else if (can("demand.view")) page = <BoardPage />;
  else if (can("kitting.view")) page = <KittingPage route={route} />;
  else page = <p className="note note-info">Your role has no modules in the web app yet. Use the desktop app for now.</p>;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">TOOLTRACK</div>
        <nav>
          {tabs.map((tab) => (
            <a key={tab.path} href={`#${tab.path}`} className={route.path === tab.path || (tab.path === "/kitting" && route.path.startsWith("/kitting/")) ? "active" : ""}>
              {tab.label}
            </a>
          ))}
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
      <main className="content">{page}</main>
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
