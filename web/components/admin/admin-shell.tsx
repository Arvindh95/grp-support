"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Users, Upload, Image as ImageIcon, BarChart3, ArrowLeft, LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAuth, useRequireAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/admin/users/", label: "Users", icon: Users },
  { href: "/admin/upload/", label: "Upload Documents", icon: Upload },
  { href: "/admin/images/", label: "Image Manager", icon: ImageIcon },
  { href: "/admin/audit/", label: "Audit & Usage", icon: BarChart3 },
];

export function AdminShell({ children }: { children: React.ReactNode }) {
  const { user, ready } = useRequireAuth("admin");
  const { signOut } = useAuth();
  const path = usePathname();

  if (!ready || !user) return null;

  return (
    <div className="h-screen flex">
      <aside className="w-64 shrink-0 border-r bg-muted/20 flex flex-col">
        <div className="p-4 border-b">
          <Link href="/chat/" className="text-sm text-muted-foreground hover:text-primary inline-flex items-center gap-1">
            <ArrowLeft className="h-3.5 w-3.5" />
            Back to chat
          </Link>
          <div className="mt-2 font-semibold">Admin</div>
          <div className="text-xs text-muted-foreground truncate">{user.email}</div>
        </div>

        <nav className="flex-1 p-2 space-y-1">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = path?.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex items-center gap-2 px-3 py-2 rounded-md text-sm hover:bg-accent",
                  active && "bg-accent font-medium",
                )}
              >
                <Icon className="h-4 w-4" />
                {label}
              </Link>
            );
          })}
        </nav>

        <div className="border-t p-3">
          <Button variant="ghost" size="sm" className="w-full justify-start" onClick={signOut}>
            <LogOut className="h-4 w-4" />
            Sign out
          </Button>
        </div>
      </aside>

      <main className="flex-1 overflow-y-auto">
        <div className="max-w-5xl mx-auto p-6">{children}</div>
      </main>
    </div>
  );
}
