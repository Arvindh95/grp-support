"use client";

import { useEffect, useState, type FormEvent } from "react";
import { KeyRound, Trash2, Loader2, Copy, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

type KeyRow = {
  id: string;
  name: string;
  owner: string;
  role: string;
  created_at?: number;
  last_used?: number | null;
  revoked: boolean;
};

const fmt = (ms?: number | null) =>
  ms ? new Date(ms).toLocaleString() : "—";

export default function ApiKeysPage() {
  const [keys, setKeys] = useState<KeyRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [owner, setOwner] = useState("");
  const [creating, setCreating] = useState(false);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try {
      setKeys(await api<KeyRow[]>("/api-keys"));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load keys");
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setNewKey(null);
    if (!name.trim() || !owner.trim()) {
      setError("Key name and owner email are required.");
      return;
    }
    setCreating(true);
    try {
      const r = await api<{ key: string }>("/api-keys", {
        method: "POST",
        body: JSON.stringify({ name: name.trim(), owner: owner.trim() }),
      });
      setNewKey(r.key);
      setName("");
      setOwner("");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Create failed");
    } finally {
      setCreating(false);
    }
  }

  async function onRevoke(id: string, kname: string) {
    if (!confirm(`Revoke key "${kname}"? Apps using it will stop working immediately.`))
      return;
    setBusy(true);
    setError(null);
    try {
      await api(`/api-keys/${encodeURIComponent(id)}`, { method: "DELETE" });
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Revoke failed");
    } finally {
      setBusy(false);
    }
  }

  function copyKey() {
    if (!newKey) return;
    navigator.clipboard.writeText(newKey);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">API Keys</h1>
        <p className="text-sm text-muted-foreground">
          Mint and revoke keys for the RAG-API. Callers send{" "}
          <code className="text-xs">Authorization: ApiKey &lt;key&gt;</code>.
        </p>
      </div>

      {error && <Alert variant="destructive">{error}</Alert>}

      {newKey && (
        <Alert variant="success">
          <div className="space-y-2">
            <div className="font-medium">
              Key created — copy it now. It is shown only once.
            </div>
            <div className="flex items-center gap-2">
              <code className="flex-1 break-all rounded bg-background px-2 py-1 text-xs">
                {newKey}
              </code>
              <Button size="sm" variant="outline" onClick={copyKey}>
                {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                {copied ? "Copied" : "Copy"}
              </Button>
            </div>
          </div>
        </Alert>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Mint a key</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onCreate} className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Key name</Label>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. acme-integration"
                required
              />
            </div>
            <div className="space-y-2">
              <Label>Owner (existing user email)</Label>
              <Input
                type="email"
                value={owner}
                onChange={(e) => setOwner(e.target.value)}
                placeholder="user@example.com"
                required
              />
            </div>
            <div className="sm:col-span-2">
              <Button type="submit" disabled={creating}>
                {creating ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <KeyRound className="h-4 w-4" />
                )}
                Create key
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">{keys?.length ?? "—"} keys</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {keys === null ? (
            <p className="px-6 py-8 text-sm text-muted-foreground">Loading…</p>
          ) : keys.length === 0 ? (
            <p className="px-6 py-8 text-sm text-muted-foreground">No keys yet.</p>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-muted/40 text-xs uppercase text-muted-foreground">
                <tr>
                  <th className="text-left font-medium px-4 py-2">Name</th>
                  <th className="text-left font-medium px-4 py-2">Owner</th>
                  <th className="text-left font-medium px-4 py-2">Created</th>
                  <th className="text-left font-medium px-4 py-2">Last used</th>
                  <th className="text-left font-medium px-4 py-2">Status</th>
                  <th className="text-right font-medium px-4 py-2">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {keys.map((k) => (
                  <tr key={k.id} className={k.revoked ? "opacity-50" : ""}>
                    <td className="px-4 py-3 font-medium">{k.name}</td>
                    <td className="px-4 py-3 text-muted-foreground">{k.owner}</td>
                    <td className="px-4 py-3 text-muted-foreground">{fmt(k.created_at)}</td>
                    <td className="px-4 py-3 text-muted-foreground">{fmt(k.last_used)}</td>
                    <td className="px-4 py-3">
                      <span
                        className={cn(
                          "text-xs px-1.5 py-0.5 rounded uppercase",
                          k.revoked
                            ? "bg-destructive/10 text-destructive"
                            : "bg-secondary text-secondary-foreground",
                        )}
                      >
                        {k.revoked ? "revoked" : "active"}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={k.revoked || busy}
                        onClick={() => onRevoke(k.id, k.name)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        Revoke
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
