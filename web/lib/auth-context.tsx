"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { clearSession, getToken, getUser, type User } from "./api";

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
    setUser(getToken() ? getUser() : null);
  };

  useEffect(() => {
    refresh();
    setReady(true);
  }, []);

  const signOut = () => {
    clearSession();
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
