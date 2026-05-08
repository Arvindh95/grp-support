"use client";

import { useRequireAuth, useAuth } from "@/lib/auth-context";
import { Button } from "@/components/ui/button";

export default function ChatPagePlaceholder() {
  const { user, ready } = useRequireAuth();
  const { signOut } = useAuth();
  if (!ready || !user) return null;

  return (
    <div className="min-h-screen p-8">
      <div className="max-w-3xl mx-auto space-y-4">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold">Hi, {user.name || user.email}</h1>
          <Button variant="outline" onClick={signOut}>Sign out</Button>
        </div>
        <p className="text-muted-foreground">
          Phase A is live (auth + sessions). Chat UI lands in Phase B.
        </p>
        <ul className="text-sm text-muted-foreground list-disc pl-5 space-y-1">
          <li>Role: <span className="font-mono">{user.role}</span></li>
          <li>Token persists across page reloads (cookie-based).</li>
        </ul>
      </div>
    </div>
  );
}
