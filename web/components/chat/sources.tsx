"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Book, FileText, Ticket } from "lucide-react";
import type { Source } from "@/lib/chat-types";

export function SourcesBlock({ sources }: { sources: Source[] }) {
  const [open, setOpen] = useState(false);
  if (!sources || sources.length === 0) return null;

  const manuals = sources.filter((s) => s.type === "manual");
  const tickets = sources.filter((s) => s.type === "ticket");
  const scripts = sources.filter((s) => s.type === "script");

  return (
    <div className="mt-3 text-sm border rounded-md bg-muted/30">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 w-full px-3 py-1.5 text-left text-muted-foreground hover:text-foreground"
      >
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        Sources ({sources.length})
      </button>
      {open && (
        <div className="px-3 pb-2 space-y-2">
          {manuals.length > 0 && (
            <div>
              <p className="text-xs font-medium flex items-center gap-1">
                <Book className="h-3 w-3" /> Manuals
              </p>
              <ul className="ml-4 text-xs space-y-0.5">
                {manuals.map((s, i) => (
                  <li key={i}>
                    <span className="font-medium">{s.module}</span>
                    {s.section ? <span className="text-muted-foreground"> · {s.section}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {scripts.length > 0 && (
            <div>
              <p className="text-xs font-medium flex items-center gap-1">
                <FileText className="h-3 w-3" /> Scripts
              </p>
              <ul className="ml-4 text-xs space-y-0.5">
                {scripts.map((s, i) => (
                  <li key={i}>{s.section || "(no purpose)"}</li>
                ))}
              </ul>
            </div>
          )}
          {tickets.length > 0 && (
            <div>
              <p className="text-xs font-medium flex items-center gap-1">
                <Ticket className="h-3 w-3" /> Tickets
              </p>
              <ul className="ml-4 text-xs space-y-0.5">
                {tickets.map((s, i) => (
                  <li key={i}>
                    <span className="font-mono">{s.referno}</span>
                    <span className="text-muted-foreground ml-2">
                      {s.index?.replace("rfs-tickets-", "")}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
