"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Plus, MessageSquare, Trash2, Users, LogOut, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth-context";
import { listChats, createChat, deleteChat } from "@/lib/chat-api";
import type { ChatSummary } from "@/lib/chat-types";

type SidebarProps = {
  refreshKey?: number;
  onChatsChanged?: () => void;
};

export function Sidebar({ refreshKey = 0, onChatsChanged }: SidebarProps) {
  const router = useRouter();
  const params = useSearchParams();
  const activeId = params.get("id");
  const { user, signOut } = useAuth();

  const [chats, setChats] = useState<ChatSummary[] | null>(null);
  const [creating, setCreating] = useState(false);

  async function refresh() {
    try {
      const list = await listChats();
      setChats(list);
    } catch {
      setChats([]);
    }
  }

  useEffect(() => {
    refresh();
  }, [refreshKey]);

  async function onNew() {
    setCreating(true);
    try {
      const c = await createChat("New chat");
      await refresh();
      onChatsChanged?.();
      router.push(`/chat/?id=${c.id}`);
    } finally {
      setCreating(false);
    }
  }

  async function onDelete(id: string, e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm("Delete this chat?")) return;
    try {
      await deleteChat(id);
    } catch {
      /* ignore */
    }
    await refresh();
    if (id === activeId) router.push("/chat/");
  }

  return (
    <aside className="w-72 shrink-0 border-r bg-muted/20 flex flex-col h-screen">
      <div className="p-4 border-b">
        <div className="flex items-center justify-between mb-1">
          <span className="font-semibold">🤖 GRP Support AI</span>
        </div>
        <div className="text-xs text-muted-foreground truncate">
          {user?.name || user?.email}
          <span className="ml-2 px-1.5 py-0.5 rounded bg-secondary text-secondary-foreground text-[10px] uppercase">
            {user?.role}
          </span>
        </div>
      </div>

      <div className="p-3">
        <Button onClick={onNew} disabled={creating} className="w-full" size="sm">
          {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
          New chat
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-2 space-y-1">
        {chats === null && <p className="text-xs text-muted-foreground px-2 py-1">Loading…</p>}
        {chats && chats.length === 0 && (
          <p className="text-xs text-muted-foreground px-2 py-1">No chats yet.</p>
        )}
        {chats?.map((c) => {
          const isActive = c.id === activeId;
          return (
            <Link
              key={c.id}
              href={`/chat/?id=${c.id}`}
              className={`group flex items-center gap-2 px-2 py-2 rounded-md text-sm hover:bg-accent ${isActive ? "bg-accent" : ""}`}
            >
              <MessageSquare className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="flex-1 truncate">{c.title || "New chat"}</span>
              <button
                onClick={(e) => onDelete(c.id, e)}
                className="opacity-0 group-hover:opacity-100 hover:text-destructive transition-opacity"
                aria-label="Delete chat"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </Link>
          );
        })}
      </div>

      <div className="border-t p-3 space-y-1">
        {user?.role === "admin" && (
          <Link href="/admin/users/" className="block">
            <Button variant="ghost" size="sm" className="w-full justify-start">
              <Users className="h-4 w-4" />
              Admin
            </Button>
          </Link>
        )}
        <Button variant="ghost" size="sm" className="w-full justify-start" onClick={signOut}>
          <LogOut className="h-4 w-4" />
          Sign out
        </Button>
      </div>
    </aside>
  );
}
