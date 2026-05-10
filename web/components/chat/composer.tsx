"use client";

import { useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Send, Loader2, Paperclip, X } from "lucide-react";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { uploadChatFile } from "@/lib/chat-api";

const ALLOWED_EXTS = [".pdf", ".md", ".txt", ".docx", ".png", ".jpg", ".jpeg", ".webp", ".csv"];
const MAX_BYTES = 25 * 1024 * 1024;

export type Attachment = {
  id: string;
  name: string;
  size: number;
};

type ComposerProps = {
  onSend: (text: string, attached: Attachment[]) => void;
  disabled?: boolean;
  placeholder?: string;
};

export function Composer({ onSend, disabled, placeholder }: ComposerProps) {
  const [value, setValue] = useState("");
  const [attached, setAttached] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  function submit(e?: FormEvent) {
    e?.preventDefault();
    const v = value.trim();
    if (!v || disabled || uploading) return;
    onSend(v, attached);
    setValue("");
    setAttached([]);
    setError(null);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  async function onFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setError(null);
    setUploading(true);
    const accepted: Attachment[] = [];
    for (const f of Array.from(files)) {
      const ext = "." + (f.name.split(".").pop() || "").toLowerCase();
      if (!ALLOWED_EXTS.includes(ext)) {
        setError(`Unsupported type: ${ext}`);
        continue;
      }
      if (f.size > MAX_BYTES) {
        setError(`Too large: ${f.name} (max 25 MB)`);
        continue;
      }
      try {
        const r = await uploadChatFile(f);
        accepted.push(r);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Upload failed");
      }
    }
    setAttached((prev) => [...prev, ...accepted]);
    setUploading(false);
    if (fileRef.current) fileRef.current.value = "";
  }

  function removeAttachment(id: string) {
    setAttached((prev) => prev.filter((a) => a.id !== id));
  }

  return (
    <form onSubmit={submit} className="border-t bg-background p-3">
      <div className="max-w-4xl mx-auto space-y-2">
        {error && <p className="text-xs text-destructive">{error}</p>}
        {(attached.length > 0 || uploading) && (
          <div className="flex flex-wrap gap-2">
            {attached.map((a) => (
              <span
                key={a.id}
                className="inline-flex items-center gap-1 text-xs bg-muted rounded px-2 py-1"
              >
                <Paperclip className="h-3 w-3" />
                <span className="max-w-[200px] truncate">{a.name}</span>
                <button
                  type="button"
                  onClick={() => removeAttachment(a.id)}
                  className="hover:text-destructive ml-1"
                  aria-label="Remove attachment"
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
            {uploading && (
              <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" />
                Uploading…
              </span>
            )}
          </div>
        )}
        <div className="flex gap-2 items-end">
          <input
            ref={fileRef}
            type="file"
            multiple
            accept={ALLOWED_EXTS.join(",")}
            className="hidden"
            onChange={(e) => onFiles(e.target.files)}
          />
          <Button
            type="button"
            variant="outline"
            size="icon"
            className="h-10 w-10 shrink-0"
            onClick={() => fileRef.current?.click()}
            disabled={disabled || uploading}
            title="Attach file (PDF, MD, TXT, DOCX, image, CSV — max 25 MB)"
          >
            <Paperclip className="h-4 w-4" />
          </Button>
          <Textarea
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={placeholder ?? "Ask a question, attach a file with the paperclip…"}
            disabled={disabled}
            rows={2}
            className="resize-none"
          />
          <Button
            type="submit"
            disabled={disabled || uploading || !value.trim()}
            size="icon"
            className="h-10 w-10 shrink-0"
          >
            {disabled ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          </Button>
        </div>
      </div>
    </form>
  );
}
