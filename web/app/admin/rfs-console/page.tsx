"use client";

import { useState } from "react";
import { FlaskConical, Loader2, Paperclip } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";

type Job = {
  job_id: string;
  status: string;
  result?: unknown;
  error?: { code: string; message: string } | null;
};

const POLL_INTERVAL_MS = 3000;
const POLL_MAX_TRIES = 80;

export default function RfsConsolePage() {
  const [apiKey, setApiKey] = useState("");
  const [lodgeId, setLodgeId] = useState("");
  const [notes, setNotes] = useState("");
  const [priority, setPriority] = useState("normal");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusLine, setStatusLine] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);

  function onPickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    setFiles(e.target.files ? Array.from(e.target.files) : []);
  }

  async function onSubmit() {
    setError(null);
    setJob(null);
    setStatusLine(null);
    if (!apiKey.trim()) return setError("An API key is required.");
    if (!lodgeId.trim() || !notes.trim())
      return setError("Lodge ID and notes are required.");

    setBusy(true);
    const auth = { Authorization: `ApiKey ${apiKey.trim()}` };
    try {
      const fd = new FormData();
      fd.append(
        "payload",
        JSON.stringify({
          rfs: { lodge_id: lodgeId.trim(), notes: notes.trim() },
          priority,
        }),
      );
      for (const f of files) fd.append("files", f);

      const submitRes = await fetch("/rag/rfs/analyze", {
        method: "POST",
        headers: auth,
        body: fd,
      });
      const submitJson = await submitRes.json();
      if (!submitRes.ok) {
        throw new Error(
          submitJson?.error?.message || `Submit failed (HTTP ${submitRes.status})`,
        );
      }

      const jobId: string = submitJson.job_id;
      setStatusLine(`Job ${jobId} queued — polling…`);

      for (let i = 0; i < POLL_MAX_TRIES; i++) {
        await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
        const pollRes = await fetch(`/rag/jobs/${jobId}`, { headers: auth });
        const j: Job = await pollRes.json();
        setStatusLine(`Job ${jobId}: ${j.status}`);
        if (["succeeded", "failed", "cancelled"].includes(j.status)) {
          setJob(j);
          return;
        }
      }
      setError("Timed out waiting for the job to finish.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Submit failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">RFS Test Console</h1>
        <p className="text-sm text-muted-foreground">
          Submit an RFS — with optional file attachments — straight to the
          RAG-API and watch the result. Uses an API key (mint one under API Keys).
        </p>
      </div>

      {error && <Alert variant="destructive">{error}</Alert>}

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Submit an RFS</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>API key</Label>
            <Input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="grp_…"
              autoComplete="off"
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Lodge ID</Label>
              <Input
                value={lodgeId}
                onChange={(e) => setLodgeId(e.target.value)}
                placeholder="LDG-12345"
              />
            </div>
            <div className="space-y-2">
              <Label>Priority</Label>
              <select
                value={priority}
                onChange={(e) => setPriority(e.target.value)}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              >
                <option value="low">low</option>
                <option value="normal">normal</option>
                <option value="high">high</option>
              </select>
            </div>
          </div>

          <div className="space-y-2">
            <Label>Notes</Label>
            <Textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={4}
              placeholder="Describe the problem. The richer the notes, the better the classification."
            />
          </div>

          <div className="space-y-2">
            <Label className="flex items-center gap-1">
              <Paperclip className="h-3.5 w-3.5" />
              Attachments (PDF, image, text/CSV — Word/Excel rejected)
            </Label>
            <input
              type="file"
              multiple
              onChange={onPickFiles}
              accept=".pdf,.png,.jpg,.jpeg,.gif,.webp,.txt,.csv,.md"
              className="block w-full text-sm text-muted-foreground file:mr-3 file:rounded-md file:border file:border-input file:bg-background file:px-3 file:py-1.5 file:text-sm"
            />
            {files.length > 0 && (
              <div className="text-xs text-muted-foreground">
                {files.length} file{files.length > 1 ? "s" : ""}:{" "}
                {files.map((f) => f.name).join(", ")}
              </div>
            )}
          </div>

          <Button onClick={onSubmit} disabled={busy}>
            {busy ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <FlaskConical className="h-4 w-4" />
            )}
            Submit &amp; poll
          </Button>
        </CardContent>
      </Card>

      {statusLine && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Result</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="text-sm font-medium">{statusLine}</div>
            {job && (
              <pre className="text-xs bg-muted/40 rounded p-3 overflow-x-auto max-h-[28rem]">
                {JSON.stringify(job.result ?? job.error ?? job, null, 2)}
              </pre>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
