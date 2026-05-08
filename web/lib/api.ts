// Thin fetch wrapper that injects the JWT and centralises 401 handling.
// All backend calls in the app go through `api()`.

const TOKEN_COOKIE = "grp_jwt";
const USER_COOKIE = "grp_user";

// Default: same-origin /api proxied by nginx to FastAPI on 127.0.0.1:8000.
// Override with NEXT_PUBLIC_API_URL when developing against a remote backend.
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ||
  (typeof window !== "undefined" ? `${window.location.origin}/api` : "/api");

export function getToken(): string | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.match(new RegExp(`(?:^|; )${TOKEN_COOKIE}=([^;]*)`));
  return m ? decodeURIComponent(m[1]) : null;
}

export type User = { email: string; role: string; name?: string };

export function getUser(): User | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.match(new RegExp(`(?:^|; )${USER_COOKIE}=([^;]*)`));
  if (!m) return null;
  try {
    return JSON.parse(decodeURIComponent(m[1])) as User;
  } catch {
    return null;
  }
}

export function setSession(token: string, user: User, hours = 12) {
  const exp = new Date(Date.now() + hours * 3600 * 1000).toUTCString();
  document.cookie = `${TOKEN_COOKIE}=${encodeURIComponent(token)}; path=/; expires=${exp}; SameSite=Lax`;
  document.cookie = `${USER_COOKIE}=${encodeURIComponent(JSON.stringify(user))}; path=/; expires=${exp}; SameSite=Lax`;
}

export function clearSession() {
  document.cookie = `${TOKEN_COOKIE}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT`;
  document.cookie = `${USER_COOKIE}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT`;
}

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

type ApiOpts = RequestInit & { skipAuth?: boolean };

export async function api<T = unknown>(path: string, opts: ApiOpts = {}): Promise<T> {
  const headers = new Headers(opts.headers);
  if (!opts.skipAuth) {
    const tok = getToken();
    if (tok) headers.set("Authorization", `Bearer ${tok}`);
  }
  if (opts.body && !headers.has("Content-Type") && !(opts.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(`${API_URL}${path}`, { ...opts, headers });

  if (res.status === 401 && !opts.skipAuth) {
    clearSession();
    if (typeof window !== "undefined" && window.location.pathname !== "/login/") {
      window.location.href = "/login/";
    }
  }

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}
