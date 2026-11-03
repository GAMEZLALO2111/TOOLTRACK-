import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, clearSession, getSession, saveSession, setUnauthorizedHandler, type Session } from "./api";

interface LoginResponse {
  token: string;
  expires_at: string;
  username: string;
  role: string;
  permissions: string[];
}

interface AuthValue {
  session: Session | null;
  expired: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (permission: string) => boolean;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(() => getSession());
  const [expired, setExpired] = useState(false);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setSession(null);
      setExpired(true);
    });
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const result = await api<LoginResponse>("POST", "/login", { username, password });
    const next: Session = {
      token: result.token,
      username: result.username,
      role: result.role,
      permissions: result.permissions,
      expiresAt: result.expires_at,
    };
    saveSession(next);
    setExpired(false);
    setSession(next);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api("POST", "/logout");
    } catch {
      /* si el servidor no responde igual se cierra la sesión local */
    }
    clearSession();
    setExpired(false);
    setSession(null);
  }, []);

  const value = useMemo<AuthValue>(
    () => ({ session, expired, login, logout, can: (permission) => session?.permissions.includes(permission) ?? false }),
    [session, expired, login, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
