import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { ConfirmDialog, Dialog, ErrorNote } from "../components/Dialog";
import { ROLE_INFO } from "../labels";
import type { UserRow } from "../types";

const ROLES = [
  { value: "SYSTEM_ADMIN", label: "System administrator" },
  { value: "ENGINEERING", label: "Engineering" },
  { value: "PLANNING", label: "Planning" },
  { value: "TOOLCRIB_SUPERVISOR", label: "Tool Crib supervisor" },
  { value: "TOOLCRIB_OPERATOR", label: "Tool Crib operator" },
  { value: "MAINTENANCE_TECHNICIAN", label: "Tool Crib / maintenance technician" },
  { value: "PUNCHING_OPERATOR", label: "Punzonado operator" },
  { value: "PUNCHING_TECHNICIAN", label: "Punzonado technician" },
] as const;
const SHIFTS = ["Turno 1", "Turno 2", "Turno 3", "Turno Mixto"] as const;

type Action =
  | { kind: "new" }
  | { kind: "edit"; user: UserRow }
  | { kind: "password"; user: UserRow }
  | { kind: "toggle"; user: UserRow }
  | { kind: "reset-others" };

export function UsersPage() {
  const { session } = useAuth();
  const [users, setUsers] = useState<UserRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
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

  async function toggleUser(user: UserRow) {
    try {
      await api("PATCH", `/users/${encodeURIComponent(user.username)}`, { activo: !user.activo });
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The change could not be saved.");
    }
  }

  async function deactivateOthers() {
    try {
      const result = await api<{ deactivated: number }>("POST", "/users/deactivate-others");
      setMessage(`${result.deactivated} other account(s) deactivated. User history was retained.`);
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Other accounts could not be deactivated.");
    }
  }

  return (
    <section className="page">
      <div className="board-bar">
        <h1>User administration</h1>
        <div className="bar-right">
          <button className="btn btn-danger" onClick={() => setAction({ kind: "reset-others" })}>
            Deactivate all except me
          </button>
          <button className="btn btn-primary" onClick={() => setAction({ kind: "new" })}>
            New user
          </button>
        </div>
      </div>
      <ErrorNote message={error} />
      {message && <div className="note note-info">{message}</div>}
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Username</th>
              <th>Employee profile</th>
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
                <td>
                  <strong>{user.full_name || "Profile incomplete"}</strong>
                  <div className="muted small">
                    {[user.employee_number && `ID ${user.employee_number}`, user.area, user.shift]
                      .filter(Boolean)
                      .join(" · ")}
                  </div>
                </td>
                <td>{ROLES.find((role) => role.value === user.role)?.label ?? user.role}</td>
                <td>{user.activo ? "Active" : "Inactive"}</td>
                <td className="hide-sm muted">{ROLE_INFO[user.role] ?? ""}</td>
                <td className="row-actions">
                  <button className="btn" onClick={() => setAction({ kind: "edit", user })}>Edit user</button>
                  <button className="btn" onClick={() => setAction({ kind: "password", user })}>Password</button>
                  <button
                    className="btn"
                    onClick={() => setAction({ kind: "toggle", user })}
                    disabled={user.username === session?.username && user.activo}
                  >
                    {user.activo ? "Deactivate" : "Reactivate"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {action?.kind === "new" && (
        <UserForm mode="new" onClose={() => setAction(null)} onSaved={() => { setAction(null); void load(); }} />
      )}
      {action?.kind === "edit" && (
        <UserForm mode="edit" user={action.user} onClose={() => setAction(null)} onSaved={() => { setAction(null); void load(); }} />
      )}
      {action?.kind === "password" && (
        <UserForm mode="password" user={action.user} onClose={() => setAction(null)} onSaved={() => { setAction(null); void load(); }} />
      )}
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
            await toggleUser(target);
          }}
        />
      )}
      {action?.kind === "reset-others" && (
        <ConfirmDialog
          title="Deactivate all other accounts"
          message={`This deactivates every account except ${session?.username} and revokes their sessions. Their audit history remains, and their employee numbers can be used for new accounts.`}
          confirmLabel="Deactivate all others"
          danger
          onClose={() => setAction(null)}
          onConfirm={async () => {
            setAction(null);
            await deactivateOthers();
          }}
        />
      )}
    </section>
  );
}

type UserFormProps =
  | { mode: "new"; user?: undefined; onClose: () => void; onSaved: () => void }
  | { mode: "edit" | "password"; user: UserRow; onClose: () => void; onSaved: () => void };

function UserForm({ mode, user, onClose, onSaved }: UserFormProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState(user?.role ?? "");
  const [profile, setProfile] = useState({
    employee_number: user?.employee_number ?? "",
    full_name: user?.full_name ?? "",
    area: user?.area ?? "",
    shift: user?.shift ?? "",
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if ((mode === "new" || mode === "password") && password.trim().length < 6) {
      setError("The password needs at least 6 characters.");
      return;
    }
    if (mode === "new" && !username.trim()) {
      setError("Enter a username.");
      return;
    }
    if (mode !== "password" && Object.values(profile).some((value) => !value.trim())) {
      setError("Complete all employee profile fields.");
      return;
    }
    if (mode !== "password" && !role) {
      setError("Select a role.");
      return;
    }
    setBusy(true);
    try {
      if (mode === "new") {
        await api("POST", "/users", { username: username.trim(), password, role, ...profile });
      } else if (mode === "edit") {
        await api("PATCH", `/users/${encodeURIComponent(user.username)}`, { role, ...profile });
      } else {
        await api("PATCH", `/users/${encodeURIComponent(user.username)}`, { password });
      }
      onSaved();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The change could not be saved.");
      setBusy(false);
    }
  }

  const title = mode === "new"
    ? "New user"
    : mode === "edit"
      ? `Edit user: ${user.username}`
      : `Reset password: ${user.username}`;
  const showUserFields = mode === "new" || mode === "edit";

  return (
    <Dialog title={title} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {mode === "new" && (
          <label>
            Username
            <input value={username} onChange={(event) => setUsername(event.target.value)} autoFocus autoCapitalize="none" required />
          </label>
        )}
        {(mode === "new" || mode === "password") && (
          <label>
            Password (min. 6)
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoFocus={mode === "password"}
              autoComplete="new-password"
              required
              minLength={6}
            />
          </label>
        )}
        {showUserFields && (
          <>
            <label>
              Role
              <select value={role} onChange={(event) => setRole(event.target.value)} required>
                <option value="" disabled>Select a role</option>
                {ROLES.map((item) => (
                  <option key={item.value} value={item.value}>{item.label}</option>
                ))}
              </select>
              <small className="muted">{ROLE_INFO[role]}</small>
            </label>
            <label>
              Employee number
              <input
                value={profile.employee_number}
                onChange={(event) => setProfile({ ...profile, employee_number: event.target.value })}
                required
              />
            </label>
            <label>
              Full name
              <input
                value={profile.full_name}
                onChange={(event) => setProfile({ ...profile, full_name: event.target.value })}
                required
              />
            </label>
            <label>
              Area
              <input
                value={profile.area}
                onChange={(event) => setProfile({ ...profile, area: event.target.value })}
                required
              />
            </label>
            <label>
              Shift
              <select
                value={profile.shift}
                onChange={(event) => setProfile({ ...profile, shift: event.target.value })}
                required
              >
                <option value="" disabled>Select a shift</option>
                {SHIFTS.map((shift) => <option key={shift} value={shift}>{shift}</option>)}
              </select>
            </label>
          </>
        )}
        <ErrorNote message={error} />
        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>Save</button>
        </div>
      </form>
    </Dialog>
  );
}
