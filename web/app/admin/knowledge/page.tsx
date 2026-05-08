"use client";

import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Loader2, RefreshCw, Trash2, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";
import { api } from "@/lib/api";

type FileEntry = { name: string; chunks: number };
type IndexEntry = { doc_count: number; files: FileEntry[] };
type KB = Record<string, IndexEntry>;

export default function KnowledgePage() {
  const [kb, setKb] = useState<KB | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);

  async function load() {
    setError(null);
    try {
      const data = await api<KB>("/knowledge-base");
      setKb(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    }
  }

  useEffect(() => {
    load();
  }, []);

  function toggle(idx: string) {
    setOpen((o) => ({ ...o, [idx]: !o[idx] }));
  }

  async function onDeleteFile(indexName: string, sourceFile: string) {
    if (!confirm(`Remove "${sourceFile}" from ${indexName}? All chunks from this file will be deleted.`)) return;
    setBusy(true);
    setError(null);
    setOkMsg(null);
    try {
      const r = await api<{ deleted: number }>(
        `/delete-file?index_name=${encodeURIComponent(indexName)}&source_file=${encodeURIComponent(sourceFile)}`,
        { method: "DELETE" },
      );
      setOkMsg(`Deleted ${r.deleted} chunks from ${sourceFile}.`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  }

  async function onDeleteIndex(indexName: string) {
    const phrase = `delete ${indexName}`;
    const typed = prompt(
      `⚠ This permanently deletes the entire index ${indexName} and ALL its data.\n\nType "${phrase}" to confirm:`,
    );
    if (typed !== phrase) {
      if (typed !== null) setError("Confirmation phrase did not match. Index NOT deleted.");
      return;
    }
    setBusy(true);
    setError(null);
    setOkMsg(null);
    try {
      await api(`/delete-index/${encodeURIComponent(indexName)}`, { method: "DELETE" });
      setOkMsg(`Index ${indexName} deleted.`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  }

  const totalDocs = kb ? Object.values(kb).reduce((s, e) => s + (e.doc_count || 0), 0) : 0;
  const totalFiles = kb ? Object.values(kb).reduce((s, e) => s + (e.files?.length || 0), 0) : 0;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Knowledge Base</h1>
          <p className="text-sm text-muted-foreground">
            What's currently indexed and searchable. Delete a single file or an entire index.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={busy}>
          <RefreshCw className="h-3.5 w-3.5" />
          Refresh
        </Button>
      </div>

      {error && <Alert variant="destructive">{error}</Alert>}
      {okMsg && <Alert variant="success">{okMsg}</Alert>}

      <div className="grid grid-cols-3 gap-4">
        <Stat label="Indices" value={kb ? Object.keys(kb).length.toString() : "—"} />
        <Stat label="Total docs" value={kb ? totalDocs.toLocaleString() : "—"} />
        <Stat label="Tracked files" value={kb ? totalFiles.toString() : "—"} />
      </div>

      {kb === null && (
        <Card>
          <CardContent className="py-8 text-sm text-muted-foreground">Loading…</CardContent>
        </Card>
      )}

      {kb && Object.entries(kb).map(([indexName, entry]) => {
        const isOpen = open[indexName] ?? false;
        return (
          <Card key={indexName}>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="text-lg">
                  <button
                    type="button"
                    onClick={() => toggle(indexName)}
                    className="inline-flex items-center gap-1 hover:text-primary"
                  >
                    {isOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                    <span className="font-mono">{indexName}</span>
                  </button>
                  <span className="text-sm font-normal text-muted-foreground ml-2">
                    {entry.doc_count.toLocaleString()} docs · {entry.files?.length || 0} files
                  </span>
                </CardTitle>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onDeleteIndex(indexName)}
                  disabled={busy}
                  className="text-destructive hover:bg-destructive/10"
                >
                  <AlertTriangle className="h-3.5 w-3.5" />
                  Drop index
                </Button>
              </div>
            </CardHeader>
            {isOpen && (
              <CardContent className="pt-0">
                {!entry.files || entry.files.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    No <code>source_file</code> tracked. (Older docs indexed before file tracking.)
                  </p>
                ) : (
                  <div className="border rounded">
                    <table className="w-full text-sm">
                      <thead className="bg-muted/40 text-xs uppercase text-muted-foreground">
                        <tr>
                          <th className="text-left font-medium px-3 py-2">File</th>
                          <th className="text-right font-medium px-3 py-2">Chunks</th>
                          <th className="text-right font-medium px-3 py-2 w-24">Action</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y">
                        {entry.files.map((f) => (
                          <tr key={f.name}>
                            <td className="px-3 py-2 font-mono text-xs break-all">{f.name}</td>
                            <td className="px-3 py-2 text-right">{f.chunks}</td>
                            <td className="px-3 py-2 text-right">
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => onDeleteFile(indexName, f.name)}
                                disabled={busy}
                                className="text-destructive hover:bg-destructive/10"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </Button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </CardContent>
            )}
          </Card>
        );
      })}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="border rounded p-3">
      <p className="text-xs text-muted-foreground uppercase tracking-wide">{label}</p>
      <p className="text-lg font-semibold mt-0.5">{value}</p>
    </div>
  );
}
