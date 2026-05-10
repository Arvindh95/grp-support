"use client";

import { useState, type FormEvent } from "react";
import { Loader2, Upload as UploadIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";
import { api, API_URL, getToken } from "@/lib/api";

type DocType = "manual" | "rfs" | "script" | "code";

const TYPE_LABEL: Record<DocType, string> = {
  manual: "Manual (.docx / .md)",
  rfs: "RFS Tickets (.xlsx / .csv)",
  script: "SQL Script (.txt)",
  code: "Code (.py / .cs / .sql)",
};

const ACCEPT: Record<DocType, string> = {
  manual: ".docx,.md",
  rfs: ".xlsx,.csv,.xls",
  script: ".txt,.sql",
  code: ".py,.cs,.sql",
};

const META_LABEL: Record<DocType, string> = {
  manual: "Module name (optional — auto-detected from filename)",
  rfs: "Target index (optional — auto-detected from timestamps)",
  script: "Purpose (optional)",
  code: "Purpose (optional)",
};

type ResultLine = {
  filename: string;
  ok: boolean;
  detail: string;
};

export default function UploadPage() {
  const [docType, setDocType] = useState<DocType>("manual");
  const [files, setFiles] = useState<File[]>([]);
  const [meta, setMeta] = useState("");
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<ResultLine[]>([]);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setFiles([]);
    setMeta("");
    setResults([]);
    setError(null);
  }

  async function uploadOne(file: File): Promise<ResultLine> {
    const tok = getToken();
    const fd = new FormData();
    fd.append("file", file);

    const url = `${API_URL}/upload-document`;
    fd.append("doc_type", docType);
    const m: Record<string, unknown> = { confirm: true };
    if (docType === "manual" && meta.trim()) m.module = meta.trim();
    if (docType === "rfs" && meta.trim()) m.index = meta.trim();
    if ((docType === "script" || docType === "code") && meta.trim()) m.purpose = meta.trim();
    fd.append("metadata", JSON.stringify(m));

    try {
      const res = await fetch(url, {
        method: "POST",
        headers: tok ? { Authorization: `Bearer ${tok}` } : {},
        body: fd,
      });
      if (!res.ok) {
        let detail = res.statusText;
        try {
          const j = await res.json();
          detail = j.detail || detail;
        } catch {
          /* ignore */
        }
        return { filename: file.name, ok: false, detail };
      }
      const j = (await res.json()) as Record<string, unknown>;
      return {
        filename: file.name,
        ok: true,
        detail: `${j.chunks_indexed ?? 0} chunk(s) indexed (errors: ${j.errors ?? 0})${
          j.detected_month ? `, month=${j.detected_month}` : ""
        }`,
      };
    } catch (e) {
      return { filename: file.name, ok: false, detail: e instanceof Error ? e.message : "Failed" };
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (files.length === 0) return;
    setBusy(true);
    setResults([]);
    setError(null);
    const out: ResultLine[] = [];
    for (const f of files) {
      const r = await uploadOne(f);
      out.push(r);
      setResults([...out]);
    }
    setBusy(false);
  }

  const single = docType === "rfs" || docType === "script" || docType === "code";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Upload Documents</h1>
        <p className="text-sm text-muted-foreground">
          Add knowledge to the AI. Files are parsed, embedded, and indexed into Elasticsearch.
        </p>
      </div>

      {error && <Alert variant="destructive">{error}</Alert>}

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">📤 New upload</CardTitle>
          <CardDescription>Pick a type, attach file(s), submit.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label>Document type</Label>
              <select
                value={docType}
                onChange={(e) => {
                  setDocType(e.target.value as DocType);
                  reset();
                }}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              >
                {Object.entries(TYPE_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </div>

            {META_LABEL[docType] && (
              <div className="space-y-2">
                <Label>{META_LABEL[docType]}</Label>
                <Input value={meta} onChange={(e) => setMeta(e.target.value)} />
              </div>
            )}

            <div className="space-y-2">
              <Label>File{single ? "" : "s"}</Label>
              <Input
                type="file"
                multiple={!single}
                accept={ACCEPT[docType]}
                onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
              />
              {files.length > 0 && (
                <p className="text-xs text-muted-foreground">{files.length} file(s) selected</p>
              )}
            </div>

            <Button type="submit" disabled={busy || files.length === 0}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <UploadIcon className="h-4 w-4" />}
              Upload {files.length > 1 ? `${files.length} files` : ""}
            </Button>
          </form>
        </CardContent>
      </Card>

      {results.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Results</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            {results.map((r, i) => (
              <div key={i} className="text-sm flex items-start gap-2">
                <span className={r.ok ? "text-green-600" : "text-destructive"}>{r.ok ? "✅" : "❌"}</span>
                <span className="font-medium">{r.filename}</span>
                <span className="text-muted-foreground">— {r.detail}</span>
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
