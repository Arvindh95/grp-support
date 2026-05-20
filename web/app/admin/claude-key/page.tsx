"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Sparkles, Loader2, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";
import { api } from "@/lib/api";

type Status = {
  configured: boolean;
  source: "ui" | "environment";
  hint: string | null;
  updated_at: number | null;
  updated_by: string | null;
};

const fmt = (ms?: number | null) =>
  ms ? new Date(ms).toLocaleString() : "—";

export default function ClaudeKeyPage() {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);
  const [key, setKey] = useState("");
  const [saving, setSaving] = useState(false);

  async function refresh() {
    try {
      setStatus(await api<Status>("/settings/anthropic-key"));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load status");
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function onSave(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setOkMsg(null);
    if (!key.trim()) {
      setError("Enter a Claude API key.");
      return;
    }
    setSaving(true);
    try {
      const r = await api<{ hint: string }>("/settings/anthropic-key", {
        method: "PUT",
        body: JSON.stringify({ key: key.trim() }),
      });
      setOkMsg(
        `Key validated and saved (${r.hint}). It takes effect on the chatbot ` +
          `and the RAG-API within ~60 seconds — no restart needed.`,
      );
      setKey("");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Claude API Key</h1>
        <p className="text-sm text-muted-foreground">
          The Anthropic API key used by the support chatbot and the RAG-API
          pipeline. A key set here overrides the server environment and takes
          effect within ~60 seconds — no restart.
        </p>
      </div>

      {error && <Alert variant="destructive">{error}</Alert>}
      {okMsg && <Alert variant="success">{okMsg}</Alert>}

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Current status</CardTitle>
        </CardHeader>
        <CardContent>
          {status === null ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : (
            <ul className="text-sm space-y-1.5">
              <li className="flex items-center gap-2">
                {status.configured ? (
                  <CheckCircle2 className="h-4 w-4 text-green-600" />
                ) : (
                  <span className="inline-block h-4 w-4" />
                )}
                <span className="font-medium">
                  {status.configured
                    ? "A Claude key is configured"
                    : "No key set in the UI"}
                </span>
              </li>
              <li className="text-muted-foreground pl-6">
                Source:{" "}
                {status.source === "ui"
                  ? "set via this page"
                  : "server environment (fallback)"}
              </li>
              {status.hint && (
                <li className="text-muted-foreground pl-6">
                  Key: <code className="text-xs">{status.hint}</code>
                </li>
              )}
              {status.configured && (
                <li className="text-muted-foreground pl-6">
                  Updated {fmt(status.updated_at)}
                  {status.updated_by ? ` by ${status.updated_by}` : ""}
                </li>
              )}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Set a new key</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSave} className="space-y-4">
            <div className="space-y-2">
              <Label>Claude API key</Label>
              <Input
                type="password"
                value={key}
                autoComplete="off"
                onChange={(e) => setKey(e.target.value)}
                placeholder="sk-ant-…"
              />
              <p className="text-xs text-muted-foreground">
                The key is validated against Anthropic before it is saved — a
                rejected key is never stored. Once saved it is kept server-side
                and never shown again.
              </p>
            </div>
            <Button type="submit" disabled={saving}>
              {saving ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Sparkles className="h-4 w-4" />
              )}
              Validate &amp; save
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
