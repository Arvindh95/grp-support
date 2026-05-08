import { api, API_URL, getToken } from "./api";
import type { ChatDoc, ChatSummary, Message, QueryRequest, Source, ImageItem } from "./chat-types";

export async function listChats(): Promise<ChatSummary[]> {
  return api<ChatSummary[]>("/chats");
}

export async function getChat(id: string): Promise<ChatDoc> {
  return api<ChatDoc>(`/chats/${id}`);
}

export async function createChat(title?: string): Promise<ChatDoc> {
  return api<ChatDoc>("/chats", { method: "POST", body: JSON.stringify({ title }) });
}

export async function updateChat(id: string, patch: { title?: string; messages?: Message[] }) {
  return api(`/chats/${id}`, { method: "PUT", body: JSON.stringify(patch) });
}

export async function deleteChat(id: string) {
  return api(`/chats/${id}`, { method: "DELETE" });
}

// Upload a single file as a one-shot attachment for the next /query.
// Returns { path, name, size } where `path` is the server-side path that the
// /query endpoint reads via attached_files[].
export async function uploadChatFile(file: File): Promise<{ path: string; name: string; size: number }> {
  const fd = new FormData();
  fd.append("file", file);
  return api("/upload-chat-file", { method: "POST", body: fd });
}

// ── Streaming /query/stream consumer ──────────────────────────────────────────

export type StreamEvent =
  | { type: "delta"; text: string }
  | { type: "tool"; name: string; input: unknown }
  | { type: "done"; answer: string; answer_chars: number; sources: Source[]; images: ImageItem[]; tool_calls: number }
  | { type: "error"; detail: string };

export async function* streamQuery(req: QueryRequest): AsyncGenerator<StreamEvent> {
  const tok = getToken();
  const res = await fetch(`${API_URL}/query/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(tok ? { Authorization: `Bearer ${tok}` } : {}),
    },
    body: JSON.stringify(req),
  });
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || detail;
    } catch {
      /* not JSON */
    }
    yield { type: "error", detail };
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE messages are separated by a blank line.
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const ev = parseSseBlock(block);
      if (ev) yield ev;
    }
  }
}

function parseSseBlock(block: string): StreamEvent | null {
  let event = "";
  let data = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!event || !data) return null;
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(data);
  } catch {
    return null;
  }
  if (event === "delta") return { type: "delta", text: String(parsed.text ?? "") };
  if (event === "tool") return { type: "tool", name: String(parsed.name ?? ""), input: parsed.input };
  if (event === "done")
    return {
      type: "done",
      answer: String(parsed.answer ?? ""),
      answer_chars: Number(parsed.answer_chars ?? 0),
      sources: (parsed.sources as Source[]) ?? [],
      images: (parsed.images as ImageItem[]) ?? [],
      tool_calls: Number(parsed.tool_calls ?? 0),
    };
  if (event === "error") return { type: "error", detail: String(parsed.detail ?? "Stream error") };
  return null;
}
