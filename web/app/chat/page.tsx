"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Composer } from "@/components/chat/composer";
import { MessageBubble } from "@/components/chat/message";
import { getChat, updateChat, streamQuery, createChat } from "@/lib/chat-api";
import type { Message } from "@/lib/chat-types";

const SAMPLE_QUESTIONS = [
  "How to register a vendor in Account Payable?",
  "Steps to process payroll pay run",
  "How to do bank reconciliation?",
  "Common issues with AP301000 screen",
  "Find similar tickets about payroll problems",
  "How to close financial period in Fixed Asset?",
];

export default function ChatPage() {
  const router = useRouter();
  const params = useSearchParams();
  const chatId = params.get("id");

  const [messages, setMessages] = useState<Message[]>([]);
  const [title, setTitle] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const scrollerRef = useRef<HTMLDivElement>(null);

  // Load chat when id changes
  useEffect(() => {
    if (!chatId) {
      setMessages([]);
      setTitle("");
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const c = await getChat(chatId);
        if (cancelled) return;
        setMessages((c.messages ?? []) as Message[]);
        setTitle(c.title ?? "");
      } catch {
        /* 404 → maybe deleted while we were here */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [chatId]);

  // Autoscroll on new content
  useEffect(() => {
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const history = useMemo(
    () =>
      messages
        .filter((m) => !m.pending && !m.error)
        .map((m) => ({ role: m.role, content: m.content })),
    [messages],
  );

  async function send(text: string) {
    setBusy(true);

    // Ensure we have a chat to write into
    let id = chatId;
    if (!id) {
      try {
        const c = await createChat(text.slice(0, 60));
        id = c.id;
        router.replace(`/chat/?id=${id}`);
      } catch (err) {
        setBusy(false);
        return;
      }
    }

    const userMsg: Message = { role: "user", content: text };
    const pendingMsg: Message = { role: "assistant", content: "", pending: true, tool_calls: [] };
    setMessages((prev) => [...prev, userMsg, pendingMsg]);

    let accumulated = "";
    let finalAnswer = "";
    let toolCalls: { name: string; input: unknown }[] = [];
    const histForCall = [...history, { role: "user", content: text }];

    try {
      for await (const ev of streamQuery({
        question: text,
        history: histForCall.slice(-10),  // last 10 turns
        include_images: true,
      })) {
        if (ev.type === "delta") {
          accumulated += ev.text;
          setMessages((prev) => {
            const out = prev.slice();
            const last = out[out.length - 1];
            if (last && last.pending) {
              out[out.length - 1] = { ...last, content: accumulated };
            }
            return out;
          });
        } else if (ev.type === "tool") {
          toolCalls = [...toolCalls, { name: ev.name, input: ev.input }];
          setMessages((prev) => {
            const out = prev.slice();
            const last = out[out.length - 1];
            if (last && last.pending) {
              out[out.length - 1] = { ...last, tool_calls: toolCalls };
            }
            return out;
          });
        } else if (ev.type === "done") {
          finalAnswer = ev.answer;
          setMessages((prev) => {
            const out = prev.slice();
            const last = out[out.length - 1];
            if (last && last.pending) {
              out[out.length - 1] = {
                role: "assistant",
                content: ev.answer || accumulated,
                sources: ev.sources,
                images: ev.images,
                tool_calls: toolCalls,
              };
            }
            return out;
          });
        } else if (ev.type === "error") {
          setMessages((prev) => {
            const out = prev.slice();
            const last = out[out.length - 1];
            if (last && last.pending) {
              out[out.length - 1] = { role: "assistant", content: "", error: ev.detail };
            }
            return out;
          });
        }
      }
    } catch (err) {
      setMessages((prev) => {
        const out = prev.slice();
        const last = out[out.length - 1];
        if (last && last.pending) {
          out[out.length - 1] = {
            role: "assistant",
            content: "",
            error: err instanceof Error ? err.message : "Stream failed",
          };
        }
        return out;
      });
    } finally {
      setBusy(false);
    }

    // Persist chat (best-effort) — title set from first user msg if still default
    if (id) {
      const allMessages = [
        ...messages,
        userMsg,
        {
          role: "assistant",
          content: finalAnswer || accumulated,
          // We don't re-include sources/images here to keep stored chats compact;
          // they'll be re-fetched on load if your spec needs them. (For now they
          // are stored — copy from latest state.)
        } as Message,
      ];
      // Actually, store latest from state:
      try {
        // Wait a tick so the state update has flushed (best-effort).
        await new Promise((r) => setTimeout(r, 0));
        const patch: { title?: string; messages?: Message[] } = {};
        if (!title || title === "New chat") patch.title = text.slice(0, 60);
        // Use the most recent state by reading via callback
        setMessages((latest) => {
          patch.messages = latest;
          return latest;
        });
        await updateChat(id, patch);
      } catch {
        /* save errors are non-fatal */
      }
    }
  }

  return (
    <>
      <div ref={scrollerRef} className="flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          <div className="max-w-3xl mx-auto p-8 space-y-6">
            <div>
              <h1 className="text-2xl font-semibold mb-1">Ask GRP Support AI</h1>
              <p className="text-sm text-muted-foreground">
                Search GRP manuals, RFS tickets, fix scripts, and code. Powered by Claude + Elasticsearch.
              </p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-muted-foreground mb-2">Try asking</p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {SAMPLE_QUESTIONS.map((q) => (
                  <button
                    key={q}
                    type="button"
                    onClick={() => send(q)}
                    disabled={busy}
                    className="text-left text-sm border rounded-md px-3 py-2 hover:bg-accent disabled:opacity-50"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="max-w-4xl mx-auto p-4 space-y-4">
            {messages.map((m, i) => (
              <MessageBubble key={i} msg={m} />
            ))}
          </div>
        )}
      </div>
      <Composer onSend={send} disabled={busy} />
    </>
  );
}
