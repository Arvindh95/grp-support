"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { clearSession, getUser, type User } from "./api";

type AuthCtx = {
  user: User | null;
  ready: boolean;
  signOut: () => void;
  refresh: () => void;
};

const Ctx = createContext<AuthCtx>({
  user: null,
  ready: false,
  signOut: () => {},
  refresh: () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const router = useRouter();

  const refresh = () => {
    // The HttpOnly auth cookie isn't visible from JS — we trust the
    // user-info cookie alone here. /api 401s will trigger clearSession()
    // and bounce to /login/.
    setUser(getUser());
  };

  useEffect(() => {
    refresh();
    setReady(true);
  }, []);

  const signOut = async () => {
    await clearSession();
    setUser(null);
    router.push("/login/");
  };

  return <Ctx.Provider value={{ user, ready, signOut, refresh }}>{children}</Ctx.Provider>;
}

export function useAuth() {
  return useContext(Ctx);
}

/** Wrap a page that requires auth. Redirects to /login if no token. */
export function useRequireAuth(role?: "admin") {
  const { user, ready } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (!ready) return;
    if (!user) {
      router.replace("/login/");
      return;
    }
    if (role === "admin" && user.role !== "admin") {
      router.replace("/chat/");
    }
  }, [user, ready, role, router]);
  return { user, ready };
}
