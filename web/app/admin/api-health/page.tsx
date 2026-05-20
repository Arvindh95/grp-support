"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw, CheckCircle2, XCircle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

type ProbeResult = {
  ok: boolean;
  httpStatus: number | null;
  json: Record<string, unknown> | null;
  detail: string;
};

async function probe(url: string): Promise<ProbeResult> {
  try {
    const res = await fetch(url, { cache: "no-store" });
    const text = await res.text();
    let json: Record<string, unknown> | null = null;
    try {
      json = JSON.parse(text);
    } catch {
      /* non-JSON body */
    }
    return {
      ok: res.ok,
      httpStatus: res.status,
      json,
      detail: json ? "" : text.slice(0, 200),
    };
  } catch (e) {
    return {
      ok: false,
      httpStatus: null,
      json: null,
      detail: e instanceof Error ? e.message : "unreachable",
    };
  }
}

function Dot({ ok }: { ok: boolean }) {
  return ok ? (
    <CheckCircle2 className="h-4 w-4 text-green-600" />
  ) : (
    <XCircle className="h-4 w-4 text-destructive" />
  );
}

function ServiceCard({
  title,
  endpoint,
  result,
}: {
  title: string;
  endpoint: string;
  result: ProbeResult | null;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base flex items-center justify-between">
          <span>{title}</span>
          {result && <Dot ok={result.ok} />}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="text-xs text-muted-foreground font-mono">{endpoint}</div>
        {result === null ? (
          <div className="text-sm text-muted-foreground">Checking…</div>
        ) : (
          <>
            <div className="text-sm">
              {result.ok ? "Reachable" : "Unreachable"}
              {result.httpStatus !== null && (
                <span className="text-muted-foreground"> · HTTP {result.httpStatus}</span>
              )}
            </div>
            {result.json && (
              <pre className="text-xs bg-muted/40 rounded p-2 overflow-x-auto">
                {JSON.stringify(result.json, null, 2)}
              </pre>
            )}
            {result.detail && (
              <div className="text-xs text-destructive break-words">{result.detail}</div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

const DEP_LABELS: Record<string, string> = {
  elasticsearch: "Elasticsearch",
  ollama: "Ollama (embeddings)",
  anthropic: "Anthropic API",
  redis: "Redis (job queue)",
};

export default function ApiHealthPage() {
  const [ragHealth, setRagHealth] = useState<ProbeResult | null>(null);
  const [ragReady, setRagReady] = useState<ProbeResult | null>(null);
  const [grpHealth, setGrpHealth] = useState<ProbeResult | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [lastChecked, setLastChecked] = useState<string>("");

  const check = useCallback(async () => {
    setRefreshing(true);
    const [rh, rr, gh] = await Promise.all([
      probe("/rag/health"),
      probe("/rag/ready"),
      probe("/api/health"),
    ]);
    setRagHealth(rh);
    setRagReady(rr);
    setGrpHealth(gh);
    setLastChecked(new Date().toLocaleTimeString());
    setRefreshing(false);
  }, []);

  useEffect(() => {
    check();
    const t = setInterval(check, 15000);
    return () => clearInterval(t);
  }, [check]);

  const deps =
    (ragReady?.json?.deps as Record<string, boolean> | undefined) ?? null;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold">API Health</h1>
          <p className="text-sm text-muted-foreground">
            Live status of the RAG-API, its dependencies, and the chatbot API.
            Auto-refreshes every 15s.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={check} disabled={refreshing}>
          {refreshing ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="h-4 w-4" />
          )}
          Refresh
        </Button>
      </div>

      {lastChecked && (
        <div className="text-xs text-muted-foreground">Last checked {lastChecked}</div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <ServiceCard title="RAG-API" endpoint="GET /rag/health" result={ragHealth} />
        <ServiceCard title="RAG-API readiness" endpoint="GET /rag/ready" result={ragReady} />
        <ServiceCard title="Chatbot API" endpoint="GET /api/health" result={grpHealth} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">RAG-API dependencies</CardTitle>
        </CardHeader>
        <CardContent>
          {deps === null ? (
            <p className="text-sm text-muted-foreground">
              No dependency data — /rag/ready did not return a deps map.
            </p>
          ) : (
            <ul className="space-y-2">
              {Object.entries(deps).map(([k, v]) => (
                <li key={k} className="flex items-center gap-2 text-sm">
                  <Dot ok={!!v} />
                  <span className="font-medium">{DEP_LABELS[k] ?? k}</span>
                  <span
                    className={cn(
                      "text-xs",
                      v ? "text-green-600" : "text-destructive",
                    )}
                  >
                    {v ? "up" : "down"}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
