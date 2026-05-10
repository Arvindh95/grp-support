"use client";

import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Loader2, Search, AlertCircle } from "lucide-react";
import { SourcesBlock } from "./sources";
import { ImageGallery } from "./images";
import type { Message } from "@/lib/chat-types";

// Old chats persisted image URLs pointing at the legacy http://...:8080 host
// (port now closed, also blocked as mixed-content under HTTPS). The HMAC sig
// is path-only, so rewriting host+port keeps the signature valid.
const LEGACY_IMG_RE = /https?:\/\/173\.212\.247\.3:8080\//g;
const NEW_IMG_BASE = "https://173.212.247.3.nip.io/images/";

function rewriteLegacyImageUrls(s: string): string {
  return s.replace(LEGACY_IMG_RE, NEW_IMG_BASE);
}

export function MessageBubble({ msg }: { msg: Message }) {
  const content = useMemo(() => rewriteLegacyImageUrls(msg.content || ""), [msg.content]);
  const images = useMemo(
    () => msg.images?.map((im) => ({ ...im, url: rewriteLegacyImageUrls(im.url) })),
    [msg.images],
  );

  if (msg.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-lg bg-primary text-primary-foreground px-4 py-2 text-sm whitespace-pre-wrap">
          {msg.content}
        </div>
      </div>
    );
  }

  // Assistant
  return (
    <div className="flex justify-start">
      <div className="max-w-[90%] w-full">
        {msg.error ? (
          <div className="rounded-lg border border-destructive/50 bg-destructive/5 px-4 py-2 text-sm text-destructive flex items-start gap-2">
            <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
            <span>{msg.error}</span>
          </div>
        ) : (
          <div className="rounded-lg border bg-card px-4 py-3 text-sm">
            {msg.tool_calls && msg.tool_calls.length > 0 && (
              <div className="mb-2 flex flex-wrap gap-1">
                {msg.tool_calls.map((t, i) => (
                  <span
                    key={i}
                    className="inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 bg-muted rounded text-muted-foreground"
                  >
                    <Search className="h-3 w-3" />
                    {t.name}
                  </span>
                ))}
              </div>
            )}
            {content ? (
              <div className="prose prose-sm max-w-none dark:prose-invert
                              prose-headings:mt-3 prose-headings:mb-2
                              prose-p:my-2 prose-ul:my-2 prose-ol:my-2 prose-li:my-0
                              prose-img:my-2 prose-img:rounded prose-img:border">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
              </div>
            ) : msg.pending ? (
              <div className="flex items-center gap-2 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                <span className="text-xs">Thinking…</span>
              </div>
            ) : null}
            {msg.context_used ? (
              <p className="text-[11px] text-muted-foreground mt-2">{msg.context_used} chunks retrieved</p>
            ) : null}
            {msg.sources && msg.sources.length > 0 && <SourcesBlock sources={msg.sources} />}
            {images && images.length > 0 && <ImageGallery images={images} />}
          </div>
        )}
      </div>
    </div>
  );
}
