"use client";

import { useState, type FormEvent, type KeyboardEvent } from "react";
import { Send, Loader2 } from "lucide-react";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";

type ComposerProps = {
  onSend: (text: string) => void;
  disabled?: boolean;
  placeholder?: string;
};

export function Composer({ onSend, disabled, placeholder }: ComposerProps) {
  const [value, setValue] = useState("");

  function submit(e?: FormEvent) {
    e?.preventDefault();
    const v = value.trim();
    if (!v || disabled) return;
    onSend(v);
    setValue("");
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <form onSubmit={submit} className="border-t bg-background p-3">
      <div className="flex gap-2 max-w-4xl mx-auto items-end">
        <Textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={placeholder ?? "Ask about modules, RFS tickets, scripts… (Enter to send, Shift+Enter for newline)"}
          disabled={disabled}
          rows={2}
          className="resize-none"
        />
        <Button type="submit" disabled={disabled || !value.trim()} size="icon" className="h-10 w-10">
          {disabled ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
        </Button>
      </div>
    </form>
  );
}
