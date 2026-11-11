const SESSION_KEY = "tooltrack.session";
const TIMEOUT_MS = 15000;

export interface Session {
  token: string;
  username: string;
  employeeNumber?: string | null;
  fullName?: string | null;
  role: string;
  permissions: string[];
  expiresAt: string;
}

export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(status: number, message: string, data?: unknown) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

let onUnauthorized: () => void = () => {};

export function setUnauthorizedHandler(handler: () => void) {
  onUnauthorized = handler;
}

export function getSession(): Session | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const session = JSON.parse(raw) as Session;
    if (new Date(session.expiresAt + "Z").getTime() < Date.now()) {
      sessionStorage.removeItem(SESSION_KEY);
      return null;
    }
    return session;
  } catch {
    return null;
  }
}

export function saveSession(session: Session) {
  sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
}

export function clearSession() {
  sessionStorage.removeItem(SESSION_KEY);
}

function errorDetail(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string" && message) return message;
  }
  if (Array.isArray(detail)) {
    return detail.map((item) => (item as { msg?: string }).msg ?? String(item)).join("; ");
  }
  if (status === 403) return "Your role is not allowed to do this.";
  return `The request failed (${status}).`;
}

/** Downloads a file the server generates (needs the session token, so it cannot be a plain link). */
export async function download(path: string, fallbackName: string): Promise<void> {
  const session = getSession();
  let response: Response;
  try {
    response = await fetch(path, { headers: session ? { Authorization: `Bearer ${session.token}` } : {} });
  } catch {
    throw new ApiError(0, "Cannot reach the TOOLTRACK server. Check that it is running.");
  }
  if (response.status === 401 && session) {
    clearSession();
    onUnauthorized();
    throw new ApiError(401, "Your session ended. Please sign in again.");
  }
  if (!response.ok) {
    let parsed: unknown = null;
    try {
      parsed = await response.json();
    } catch {
      /* respuesta sin JSON */
    }
    throw new ApiError(response.status, errorDetail(parsed, response.status));
  }
  const name = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") ?? "")?.[1] ?? fallbackName;
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export async function api<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
  const session = getSession();
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (session) headers.Authorization = `Bearer ${session.token}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
  } catch {
    throw new ApiError(0, "Cannot reach the TOOLTRACK server. Check that it is running.");
  } finally {
    clearTimeout(timer);
  }

  if (response.status === 401 && session && path !== "/login") {
    clearSession();
    onUnauthorized();
    throw new ApiError(401, "Your session ended. Please sign in again.");
  }
  if (!response.ok) {
    let parsed: unknown = null;
    try {
      parsed = await response.json();
    } catch {
      /* respuesta sin JSON */
    }
    throw new ApiError(response.status, errorDetail(parsed, response.status), (parsed as { detail?: unknown } | null)?.detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
