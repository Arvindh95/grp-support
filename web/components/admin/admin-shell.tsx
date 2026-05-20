"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Users, Upload, BarChart3, ArrowLeft, LogOut, Database,
  KeyRound, Activity, BookOpen, BookText, FlaskConical,
  PanelLeftClose, PanelLeftOpen,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAuth, useRequireAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";

const NAV = [
  {
    group: "Chatbot",
    items: [
      { href: "/admin/users/", label: "Users", icon: Users },
      { href: "/admin/upload/", label: "Upload Documents", icon: Upload },
      { href: "/admin/knowledge/", label: "Knowledge Base", icon: Database },
      { href: "/admin/audit/", label: "Audit & Usage", icon: BarChart3 },
    ],
  },
  {
    group: "RAG-API",
    items: [
      { href: "/admin/api-keys/", label: "API Keys", icon: KeyRound },
      { href: "/admin/api-health/", label: "API Health", icon: Activity },
      { href: "/admin/api-guide/", label: "API Guide", icon: BookText },
      { href: "/admin/api-docs/", label: "API Reference", icon: BookOpen },
      { href: "/admin/rfs-console/", label: "RFS Console", icon: FlaskConical },
    ],
  },
];

const STORAGE_KEY = "grp_admin_sidebar_collapsed";

export function AdminShell({ children }: { children: React.ReactNode }) {
  const { user, ready } = useRequireAuth("admin");
  const { signOut } = useAuth();
  const path = usePathname();
  const [collapsed, setCollapsed] = useState(false);

  // Restore the collapsed preference after mount (localStorage is client-only).
  useEffect(() => {
    setCollapsed(localStorage.getItem(STORAGE_KEY) === "1");
  }, []);

  function toggle() {
    setCollapsed((c) => {
      const next = !c;
      localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
      return next;
    });
  }

  if (!ready || !user) return null;

  return (
    <div className="h-screen flex">
      <aside
        className={cn(
          "shrink-0 border-r bg-muted/20 flex flex-col transition-[width] duration-200",
          collapsed ? "w-16" : "w-64",
        )}
      >
        <div className="p-3 border-b">
          <div className="flex items-center justify-between gap-2">
            {!collapsed && (
              <Link
                href="/chat/"
                className="text-sm text-muted-foreground hover:text-primary inline-flex items-center gap-1"
              >
                <ArrowLeft className="h-3.5 w-3.5" />
                Back to chat
              </Link>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={toggle}
              title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              className={cn("h-7 w-7 p-0 shrink-0", collapsed && "mx-auto")}
            >
              {collapsed
                ? <PanelLeftOpen className="h-4 w-4" />
                : <PanelLeftClose className="h-4 w-4" />}
            </Button>
          </div>
          {!collapsed && (
            <>
              <div className="mt-2 font-semibold">Admin</div>
              <div className="text-xs text-muted-foreground truncate">{user.email}</div>
            </>
          )}
        </div>

        <nav className="flex-1 p-2 space-y-3 overflow-y-auto">
          {NAV.map((section) => (
            <div key={section.group} className="space-y-1">
              {!collapsed && (
                <div className="px-3 pt-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {section.group}
                </div>
              )}
              {section.items.map(({ href, label, icon: Icon }) => {
                const active = path?.startsWith(href);
                return (
                  <Link
                    key={href}
                    href={href}
                    title={collapsed ? label : undefined}
                    className={cn(
                      "flex items-center gap-2 px-3 py-2 rounded-md text-sm hover:bg-accent",
                      active && "bg-accent font-medium",
                      collapsed && "justify-center px-0",
                    )}
                  >
                    <Icon className="h-4 w-4 shrink-0" />
                    {!collapsed && label}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="border-t p-3">
          <Button
            variant="ghost"
            size="sm"
            className={cn("w-full", collapsed ? "justify-center px-0" : "justify-start")}
            onClick={signOut}
            title={collapsed ? "Sign out" : undefined}
          >
            <LogOut className="h-4 w-4" />
            {!collapsed && "Sign out"}
          </Button>
        </div>
      </aside>

      <main className="flex-1 overflow-y-auto">
        <div className="max-w-5xl mx-auto p-6">{children}</div>
      </main>
    </div>
  );
}
