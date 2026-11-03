import { useState, type FormEvent } from "react";
import { ApiError } from "../api";
import { useAuth } from "../auth";
import { ErrorNote } from "../components/Dialog";

export function LoginPage() {
  const { login, expired } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!username.trim() || !password) return setError("Enter your username and password.");
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.status === 401
            ? "Wrong username or password."
            : caught.message
          : "Could not sign in.",
      );
      setPassword("");
      setBusy(false);
    }
  }

  return (
    <main className="login-wrap">
      <form className="login" onSubmit={submit}>
        <div className="login-brand">
          <h1>TOOLTRACK</h1>
          <span>TOOL CONTROL</span>
        </div>
        {expired && <div className="note note-warn">Your session ended. Please sign in again.</div>}
        <label>
          Username
          <input value={username} onChange={(event) => setUsername(event.target.value)} autoFocus autoComplete="username" autoCapitalize="none" />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" />
        </label>
        <ErrorNote message={error} />
        <button className="btn btn-primary block big" type="submit" disabled={busy}>
          {busy ? "Signing in…" : "SIGN IN"}
        </button>
      </form>
    </main>
  );
}
