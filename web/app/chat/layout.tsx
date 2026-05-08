"use client";

import { Suspense, useState } from "react";
import { Sidebar } from "@/components/chat/sidebar";
import { useRequireAuth } from "@/lib/auth-context";

export default function ChatLayout({ children }: { children: React.ReactNode }) {
  const { user, ready } = useRequireAuth();
  const [refreshKey, setRefreshKey] = useState(0);

  if (!ready) return null;
  if (!user) return null;

  return (
    <div className="h-screen flex">
      <Suspense fallback={<aside className="w-72 shrink-0 border-r bg-muted/20" />}>
        <Sidebar refreshKey={refreshKey} onChatsChanged={() => setRefreshKey((k) => k + 1)} />
      </Suspense>
      <main className="flex-1 min-w-0 flex flex-col">
        <Suspense fallback={null}>{children}</Suspense>
      </main>
    </div>
  );
}
