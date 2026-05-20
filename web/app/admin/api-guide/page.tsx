import type { ReactNode } from "react";

/* ── Small presentational helpers ─────────────────────────────────────────── */

function Section({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-6 space-y-3">
      <h2 className="text-xl font-semibold border-b pb-1">{title}</h2>
      {children}
    </section>
  );
}

function Code({ children }: { children: string }) {
  return (
    <pre className="text-xs bg-muted/50 border rounded-md p-3 overflow-x-auto">
      <code>{children}</code>
    </pre>
  );
}

function C({ children }: { children: ReactNode }) {
  return (
    <code className="text-[0.85em] bg-muted px-1 py-0.5 rounded">{children}</code>
  );
}

const BASE = "https://173.212.247.3.nip.io/rag";

/* ── Page ─────────────────────────────────────────────────────────────────── */

export default function ApiGuidePage() {
  return (
    <div className="space-y-8 pb-12">
      <div>
        <h1 className="text-2xl font-semibold">API Guide</h1>
        <p className="text-sm text-muted-foreground">
          How the RAG-API works end to end — submitting a request, attaching
          files, polling for the result, and handling errors. For the
          interactive endpoint reference, see <strong>API Reference</strong>.
        </p>
      </div>

      {/* TOC */}
      <nav className="text-sm rounded-md border bg-muted/20 p-4">
        <div className="font-medium mb-2">Contents</div>
        <ol className="grid grid-cols-2 gap-x-6 gap-y-1 list-decimal list-inside text-muted-foreground">
          <li><a className="hover:underline" href="#overview">How it works</a></li>
          <li><a className="hover:underline" href="#auth">Base URL &amp; authentication</a></li>
          <li><a className="hover:underline" href="#submit">Submitting an RFS (JSON)</a></li>
          <li><a className="hover:underline" href="#multipart">Submitting with file attachments</a></li>
          <li><a className="hover:underline" href="#fields">Request fields</a></li>
          <li><a className="hover:underline" href="#accepted">The 202 response</a></li>
          <li><a className="hover:underline" href="#poll">Polling for the result</a></li>
          <li><a className="hover:underline" href="#result">The analysis result</a></li>
          <li><a className="hover:underline" href="#cancel">Cancelling a job</a></li>
          <li><a className="hover:underline" href="#idempotency">Idempotency</a></li>
          <li><a className="hover:underline" href="#ratelimit">Rate limits</a></li>
          <li><a className="hover:underline" href="#errors">Errors</a></li>
          <li><a className="hover:underline" href="#webhooks">Webhooks (optional)</a></li>
          <li><a className="hover:underline" href="#health">Health endpoints</a></li>
        </ol>
      </nav>

      <Section id="overview" title="1. How it works">
        <p className="text-sm">
          The RAG-API analyses a support ticket (an <strong>RFS</strong>) with a
          5-agent Claude pipeline and returns a structured, cited analysis.
          Analysis takes anywhere from a few seconds to ~90s, so the API is{" "}
          <strong>asynchronous</strong>:
        </p>
        <ol className="text-sm list-decimal list-inside space-y-1">
          <li>
            <strong>Submit</strong> the RFS to <C>POST /rfs/analyze</C>. You get
            back a <C>job_id</C> immediately (HTTP 202).
          </li>
          <li>
            <strong>Poll</strong> <C>GET /jobs/&#123;job_id&#125;</C> until{" "}
            <C>status</C> is <C>succeeded</C> (or <C>failed</C>).
          </li>
          <li>
            Read the analysis from the job&apos;s <C>result</C> field.
          </li>
        </ol>
        <p className="text-sm text-muted-foreground">
          Optionally, supply a <C>callback_url</C> and the API will POST the
          finished job to you instead — see Webhooks.
        </p>
      </Section>

      <Section id="auth" title="2. Base URL & authentication">
        <p className="text-sm">All endpoints live under:</p>
        <Code>{BASE}</Code>
        <p className="text-sm">
          Every request needs an API key in the <C>Authorization</C> header.
          Mint one on the <strong>API Keys</strong> page; it looks like{" "}
          <C>grp_xxxxxxxx</C>.
        </p>
        <Code>{`Authorization: ApiKey grp_xxxxxxxx`}</Code>
        <p className="text-sm text-muted-foreground">
          A missing or invalid key returns <C>401 unauthorized</C>. The key is
          shown only once at creation — store it securely.
        </p>
      </Section>

      <Section id="submit" title="3. Submitting an RFS (JSON)">
        <p className="text-sm">
          Send a JSON body to <C>POST /rfs/analyze</C>. Only{" "}
          <C>rfs.lodge_id</C> and <C>rfs.notes</C> are required.
        </p>
        <Code>{`curl -X POST ${BASE}/rfs/analyze \\
  -H "Authorization: ApiKey grp_xxxxxxxx" \\
  -H "Content-Type: application/json" \\
  -H "Idempotency-Key: 11111111-1111-1111-1111-111111111111" \\
  -d '{
    "rfs": {
      "lodge_id": "LDG-12345",
      "notes": "License error after renewal. Users blocked at login.",
      "relatedarea": "Licensing",
      "contactemail": "user@example.com"
    },
    "priority": "normal"
  }'`}</Code>
        <p className="text-sm text-muted-foreground">
          <C>Idempotency-Key</C> is optional but recommended — see Idempotency.
        </p>
      </Section>

      <Section id="multipart" title="4. Submitting with file attachments">
        <p className="text-sm">
          To attach files, send <C>multipart/form-data</C> instead of JSON: a
          text field <C>payload</C> holding the same JSON, plus one or more{" "}
          <C>files</C> parts.
        </p>
        <Code>{`curl -X POST ${BASE}/rfs/analyze \\
  -H "Authorization: ApiKey grp_xxxxxxxx" \\
  -F 'payload={"rfs":{"lodge_id":"LDG-12345","notes":"See attached screenshot."},"priority":"normal"}' \\
  -F "files=@/path/to/screenshot.png" \\
  -F "files=@/path/to/log.pdf"`}</Code>
        <p className="text-sm">
          Claude reads PDFs, images, and text natively — <strong>no OCR</strong>.
        </p>
        <ul className="text-sm list-disc list-inside space-y-1">
          <li>
            <strong>Allowed:</strong> PDF, PNG, JPEG, GIF, WebP, plain text,
            CSV, Markdown.
          </li>
          <li>
            <strong>Not allowed:</strong> Word (<C>.docx</C>) and Excel (
            <C>.xlsx</C>) — convert first: Word → PDF, Excel → CSV. They are
            rejected with a <C>400</C> and an explanatory message.
          </li>
          <li>
            <strong>Size:</strong> 25 MB total across all attachments.
          </li>
        </ul>
        <p className="text-sm text-muted-foreground">
          You can also attach files in pure JSON mode by adding an{" "}
          <C>attachments</C> array to <C>rfs</C>, each entry{" "}
          <C>{`{filename, content_type, content_b64}`}</C> with base64-encoded
          content.
        </p>
      </Section>

      <Section id="fields" title="5. Request fields">
        <div className="overflow-x-auto">
          <table className="w-full text-sm border">
            <thead className="bg-muted/40 text-xs uppercase text-muted-foreground">
              <tr>
                <th className="text-left font-medium px-3 py-2">Field</th>
                <th className="text-left font-medium px-3 py-2">Required</th>
                <th className="text-left font-medium px-3 py-2">Notes</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {[
                ["rfs.lodge_id", "yes", "Your ticket identifier (string)."],
                ["rfs.notes", "yes", "Problem description, 1–16000 chars. Richer notes = better classification."],
                ["rfs.relatedarea", "no", "Module / area hint, e.g. \"Fixed Assets\"."],
                ["rfs.contactemail", "no", "Reporter email."],
                ["rfs.referno / branch_id / clientid / projectid", "no", "Optional reference fields."],
                ["rfs.attachments", "no", "Inline files (JSON mode). See attachments."],
                ["priority", "no", "low | normal | high. Default normal."],
                ["callback_url", "no", "Webhook URL for the finished job."],
                ["callback_secret_hint", "no", "8–64 chars; selects the HMAC secret for the webhook."],
                ["client_metadata", "no", "Free-form object echoed back on the job."],
              ].map(([f, req, notes]) => (
                <tr key={f}>
                  <td className="px-3 py-2 font-mono text-xs">{f}</td>
                  <td className="px-3 py-2">{req}</td>
                  <td className="px-3 py-2 text-muted-foreground">{notes}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-sm text-muted-foreground">
          Unknown fields are rejected — the request schema is strict.
        </p>
      </Section>

      <Section id="accepted" title="6. The 202 response">
        <p className="text-sm">
          A successful submit returns <C>202 Accepted</C> — not the analysis.
        </p>
        <Code>{`{
  "job_id": "e6390642-0068-4eb6-a7ac-5f657b09b974",
  "status": "queued",
  "poll_url": "/jobs/e6390642-0068-4eb6-a7ac-5f657b09b974",
  "estimated_seconds": 3
}`}</Code>
        <p className="text-sm">
          Keep the <C>job_id</C> — you poll it next.
        </p>
      </Section>

      <Section id="poll" title="7. Polling for the result">
        <p className="text-sm">
          Call <C>GET /jobs/&#123;job_id&#125;</C> with the same{" "}
          <C>Authorization</C> header. Poll every ~3 seconds until the status is
          terminal.
        </p>
        <Code>{`curl ${BASE}/jobs/e6390642-0068-4eb6-a7ac-5f657b09b974 \\
  -H "Authorization: ApiKey grp_xxxxxxxx"`}</Code>
        <p className="text-sm">Status values:</p>
        <ul className="text-sm list-disc list-inside space-y-1">
          <li><C>queued</C> — waiting for a worker.</li>
          <li><C>running</C> — pipeline in progress.</li>
          <li><C>succeeded</C> — done; read <C>result</C>.</li>
          <li><C>failed</C> — see the <C>error</C> field.</li>
          <li><C>cancelled</C> — cancelled before completion.</li>
          <li><C>expired</C> — job state aged out (kept ~7 days).</li>
        </ul>
      </Section>

      <Section id="result" title="8. The analysis result">
        <p className="text-sm">
          On <C>succeeded</C>, the job carries a <C>result</C> object, plus an{" "}
          <C>agent_trace</C> (per-agent timing/tokens) and <C>usage</C>{" "}
          (token counts and estimated cost).
        </p>
        <Code>{`{
  "status": "succeeded",
  "result": {
    "category": "license-error",
    "confidence": 0.78,
    "summary": "Short restatement of the problem.",
    "likely_cause": "Root-cause hypothesis, or null.",
    "recommended_actions": [
      { "step": 1, "detail": "Concrete next step…", "source_refs": ["cit-1"] }
    ],
    "citations": [
      { "id": "cit-1", "source": "manual",
        "locator": { "module": "...", "section": "..." },
        "snippet": "Supporting sentence from the source.",
        "score": 0.9 }
    ],
    "related_rfs": [
      { "lodge_id": "LDG-90211", "score": 0.7, "snippet": "Similar past ticket." }
    ],
    "verifier_flags": [
      { "kind": "weak_citation", "detail": "Why this step is weakly supported." }
    ]
  },
  "agent_trace": [ /* classifier, planner, analyst, verifier, formatter … */ ],
  "usage": { "input_tokens": 0, "output_tokens": 0, "estimated_cost_rm": 0.0 }
}`}</Code>
        <ul className="text-sm list-disc list-inside space-y-1">
          <li>
            <C>recommended_actions[].source_refs</C> point at{" "}
            <C>citations[].id</C> — every action is traceable to evidence.
          </li>
          <li>
            <C>citations[].source</C> is <C>manual</C>, <C>rfs_ticket</C>,{" "}
            <C>code_script</C>, or <C>attachment</C> (a file you sent).
          </li>
          <li>
            <C>verifier_flags</C> are honest quality warnings — weak citations,
            low confidence, retrieval gaps. An empty list means the analysis
            passed review cleanly.
          </li>
        </ul>
      </Section>

      <Section id="cancel" title="9. Cancelling a job">
        <p className="text-sm">
          A queued or running job can be cancelled:
        </p>
        <Code>{`curl -X POST ${BASE}/jobs/{job_id}/cancel \\
  -H "Authorization: ApiKey grp_xxxxxxxx"`}</Code>
        <p className="text-sm text-muted-foreground">
          If the pipeline already finished, the result stands.
        </p>
      </Section>

      <Section id="idempotency" title="10. Idempotency">
        <p className="text-sm">
          Send an <C>Idempotency-Key</C> header (any unique string, e.g. a
          UUID) on each submit:
        </p>
        <ul className="text-sm list-disc list-inside space-y-1">
          <li>
            Same key + <strong>same</strong> body → the original job is
            returned, no duplicate work.
          </li>
          <li>
            Same key + <strong>different</strong> body → <C>409 idempotency_conflict</C>.
          </li>
          <li>Use a fresh key for each genuinely new submission.</li>
        </ul>
        <p className="text-sm text-muted-foreground">
          This makes safe retries trivial — if a submit times out, resend it
          with the same key.
        </p>
      </Section>

      <Section id="ratelimit" title="11. Rate limits">
        <p className="text-sm">
          Each key has a per-minute request budget. Every response carries:
        </p>
        <Code>{`RateLimit-Limit:     60
RateLimit-Remaining: 58
RateLimit-Reset:     41`}</Code>
        <p className="text-sm">
          Exceeding it returns <C>429 rate_limited</C>; wait for the reset
          window and retry.
        </p>
      </Section>

      <Section id="errors" title="12. Errors">
        <p className="text-sm">
          Every error uses the same envelope, with a stable machine-readable{" "}
          <C>code</C> and a <C>request_id</C> for support:
        </p>
        <Code>{`{
  "error": {
    "code": "bad_request",
    "message": "Human-readable explanation.",
    "request_id": "432ed970-5b32-4bb1-a33a-e1c3575d22db"
  }
}`}</Code>
        <div className="overflow-x-auto">
          <table className="w-full text-sm border">
            <thead className="bg-muted/40 text-xs uppercase text-muted-foreground">
              <tr>
                <th className="text-left font-medium px-3 py-2">HTTP</th>
                <th className="text-left font-medium px-3 py-2">code</th>
                <th className="text-left font-medium px-3 py-2">Meaning</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {[
                ["400", "bad_request", "Malformed body, bad field, or unsupported attachment type."],
                ["401", "unauthorized", "Missing or invalid API key."],
                ["403", "forbidden", "Key lacks permission for this action."],
                ["404", "not_found", "No such job."],
                ["409", "idempotency_conflict", "Idempotency-Key reused with a different body."],
                ["413", "payload_too_large", "Body / attachments over the size cap."],
                ["429", "rate_limited", "Per-key rate limit exceeded."],
                ["5xx", "internal / upstream_unavailable", "Server-side fault — safe to retry with the same Idempotency-Key."],
              ].map(([h, c, m]) => (
                <tr key={c}>
                  <td className="px-3 py-2 font-mono text-xs">{h}</td>
                  <td className="px-3 py-2 font-mono text-xs">{c}</td>
                  <td className="px-3 py-2 text-muted-foreground">{m}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section id="webhooks" title="13. Webhooks (optional)">
        <p className="text-sm">
          Instead of polling, set <C>callback_url</C> on the submit. When the
          job finishes, the API <C>POST</C>s the full job object to that URL.
        </p>
        <ul className="text-sm list-disc list-inside space-y-1">
          <li>
            The delivery is signed — verify the <C>X-Signature</C> HMAC header
            against your webhook secret before trusting the payload.
          </li>
          <li>
            Failed deliveries are retried with exponential backoff.
          </li>
          <li>
            Polling still works alongside webhooks — use whichever fits.
          </li>
        </ul>
      </Section>

      <Section id="health" title="14. Health endpoints">
        <p className="text-sm">No auth required:</p>
        <ul className="text-sm list-disc list-inside space-y-1">
          <li>
            <C>GET /health</C> — liveness, returns <C>{`{"status":"ok"}`}</C>.
          </li>
          <li>
            <C>GET /ready</C> — readiness, reports each dependency
            (Elasticsearch, Ollama, Anthropic, Redis).
          </li>
        </ul>
        <p className="text-sm text-muted-foreground">
          The <strong>API Health</strong> page renders these live.
        </p>
      </Section>
    </div>
  );
}
