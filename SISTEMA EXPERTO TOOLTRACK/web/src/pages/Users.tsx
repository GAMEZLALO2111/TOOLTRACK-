import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { ConfirmDialog, Dialog, ErrorNote } from "../components/Dialog";
import { ROLE_INFO } from "../labels";
import type { UserRow } from "../types";

const ROLES = Object.keys(ROLE_INFO);

type Action =
  | { kind: "new" }
  | { kind: "role"; user: UserRow }
  | { kind: "password"; user: UserRow }
  | { kind: "toggle"; user: UserRow };

export function UsersPage() {
  const { session } = useAuth();
  const [users, setUsers] = useState<UserRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [action, setAction] = useState<Action | null>(null);

  const load = useCallback(async () => {
    try {
      setUsers(await api<UserRow[]>("GET", "/users"));
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load users.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="page">
      <div className="board-bar">
        <h1>User administration</h1>
        <div className="bar-right">
          <button className="btn btn-primary" onClick={() => setAction({ kind: "new" })}>
            New user
          </button>
        </div>
      </div>
      <ErrorNote message={error} />
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Username</th>
              <th>Role</th>
              <th>Status</th>
              <th className="hide-sm">What the role can do</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((user) => (
              <tr key={user.username} className={user.activo ? "" : "inactive"}>
                <td>
                  <strong>{user.username}</strong>
                  {user.username === session?.username && <span className="muted"> (you)</span>}
                </td>
                <td>{user.role}</td>
                <td>{user.activo ? "Active" : "Inactive"}</td>
                <td className="hide-sm muted">{ROLE_INFO[user.role] ?? ""}</td>
                <td className="row-actions">
                  <button className="btn" onClick={() => setAction({ kind: "role", user })}>
                    Role
                  </button>
                  <button className="btn" onClick={() => setAction({ kind: "password", user })}>
                    Password
                  </button>
                  <button className="btn" onClick={() => setAction({ kind: "toggle", user })} disabled={user.username === session?.username && user.activo}>
                    {user.activo ? "Deactivate" : "Reactivate"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {action?.kind === "new" && <UserForm mode="new" onClose={() => setAction(null)} onSaved={() => { setAction(null); void load(); }} />}
      {action?.kind === "role" && <UserForm mode="role" user={action.user} onClose={() => setAction(null)} onSaved={() => { setAction(null); void load(); }} />}
      {action?.kind === "password" && <UserForm mode="password" user={action.user} onClose={() => setAction(null)} onSaved={() => { setAction(null); void load(); }} />}
      {action?.kind === "toggle" && (
        <ConfirmDialog
          title={action.user.activo ? "Deactivate user" : "Reactivate user"}
          message={`${action.user.activo ? "Deactivate" : "Reactivate"} ${action.user.username}? Their history is kept.`}
          confirmLabel={action.user.activo ? "Deactivate" : "Reactivate"}
          danger={action.user.activo}
          onClose={() => setAction(null)}
          onConfirm={async () => {
            const target = action.user;
            setAction(null);
            try {
              await api("PATCH", `/users/${encodeURIComponent(target.username)}`, { activo: !target.activo });
              await load();
            } catch (caught) {
              setError(caught instanceof ApiError ? caught.message : "The change could not be saved.");
            }
          }}
        />
      )}
    </section>
  );
}

type FormProps =
  | { mode: "new"; user?: undefined; onClose: () => void; onSaved: () => void }
  | { mode: "role" | "password"; user: UserRow; onClose: () => void; onSaved: () => void };

function UserForm({ mode, user, onClose, onSaved }: FormProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState(user?.role ?? "TOOLCRIB_OPERATOR");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if ((mode === "new" || mode === "password") && password.trim().length < 6) {
      return setError("The password needs at least 6 characters.");
    }
    if (mode === "new" && !username.trim()) return setError("Enter a username.");
    setBusy(true);
    try {
      if (mode === "new") await api("POST", "/users", { username: username.trim(), password, role });
      else if (mode === "role") await api("PATCH", `/users/${encodeURIComponent(user.username)}`, { role });
      else await api("PATCH", `/users/${encodeURIComponent(user.username)}`, { password });
      onSaved();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The change could not be saved.");
      setBusy(false);
    }
  }

  const title = mode === "new" ? "New user" : mode === "role" ? `Change role: ${user.username}` : `Reset password: ${user.username}`;
  return (
    <Dialog title={title} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {mode === "new" && (
          <label>
            Username
            <input value={username} onChange={(event) => setUsername(event.target.value)} autoFocus autoCapitalize="none" />
          </label>
        )}
        {(mode === "new" || mode === "password") && (
          <label>
            Password (min. 6)
            <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoFocus={mode === "password"} autoComplete="new-password" />
          </label>
        )}
        {(mode === "new" || mode === "role") && (
          <label>
            Role
            <select value={role} onChange={(event) => setRole(event.target.value)}>
              {ROLES.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
            <small className="muted">{ROLE_INFO[role]}</small>
          </label>
        )}
        <ErrorNote message={error} />
        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            Save
          </button>
        </div>
      </form>
    </Dialog>
  );
}
