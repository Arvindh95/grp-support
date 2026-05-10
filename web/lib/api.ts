// Thin fetch wrapper around the GRP API. Auth is HttpOnly cookie set by the
// backend on /auth/login — JavaScript never sees the JWT, so XSS that lands
// in the SPA cannot exfiltrate it. A non-secret `grp_user` cookie carries
// just the email/role/name for fast UI rendering.

const USER_COOKIE = "grp_user";

// Default: same-origin /api proxied by nginx to FastAPI on 127.0.0.1:8000.
// Override with NEXT_PUBLIC_API_URL when developing against a remote backend.
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ||
  (typeof window !== "undefined" ? `${window.location.origin}/api` : "/api");

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

export function setSession(_token: string, user: User, hours = 12) {
  // The JWT is set by the server as an HttpOnly cookie on /auth/login —
  // we ignore the token argument and only persist the non-secret user
  // descriptor for navigation/UI.
  const exp = new Date(Date.now() + hours * 3600 * 1000).toUTCString();
  const secure = typeof window !== "undefined" && window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${USER_COOKIE}=${encodeURIComponent(JSON.stringify(user))}; path=/; expires=${exp}; SameSite=Lax${secure}`;
}

export async function clearSession() {
  // Tell the server to clear the HttpOnly auth cookie, then drop the
  // user-info cookie locally.
  try {
    await fetch(`${API_URL}/auth/logout`, { method: "POST", credentials: "include" });
  } catch {
    /* network failure — server-side cookie persists, but client-side
       clearing below still proceeds so the SPA UI flips to logged-out */
  }
  const secure = typeof window !== "undefined" && window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${USER_COOKIE}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT; SameSite=Lax${secure}`;
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
  if (opts.body && !headers.has("Content-Type") && !(opts.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  // credentials:"include" sends the HttpOnly grp_jwt cookie; auth flows
  // entirely through the cookie, no Authorization header from the SPA.
  const res = await fetch(`${API_URL}${path}`, { ...opts, headers, credentials: "include" });

  if (res.status === 401 && !opts.skipAuth) {
    // Server rejected our cookie — drop local user state and bounce to /login/
    void clearSession();
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
