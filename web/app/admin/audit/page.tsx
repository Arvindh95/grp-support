"use client";

import { useEffect, useState } from "react";
import { Loader2, RefreshCw, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";
import { api } from "@/lib/api";

type AuditRow = {
  ts: string;
  user: string;
  event: string;
  question?: string;
  status?: string;
  model?: string;
  latency_ms?: number;
  tool_calls?: number;
  input_tokens?: number;
  output_tokens?: number;
  cached_tokens?: number;
  answer_chars?: number;
  error?: string;
};

type Usage = {
  month_start: string;
  month_end: string;
  users: {
    user: string;
    calls: number;
    input_tokens: number;
    output_tokens: number;
    cached_tokens: number;
    cost_usd: number;
  }[];
  total: {
    input_tokens: number;
    output_tokens: number;
    cached_tokens: number;
    cost_usd: number;
    budget: number;
    budget_pct: number | null;
  };
};

export default function AuditPage() {
  const [audit, setAudit] = useState<AuditRow[] | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  async function load() {
    try {
      const [a, u] = await Promise.all([
        api<AuditRow[]>("/audit?size=100"),
        api<Usage>("/audit/usage"),
      ]);
      setAudit(a);
      setUsage(u);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function runRetention() {
    setRunning(true);
    setError(null);
    setOkMsg(null);
    try {
      const r = await api<{ audit: { deleted: number }; chats: { deleted: number } }>(
        "/admin/retention/run?audit_days=90&chats_days=365",
        { method: "POST" },
      );
      setOkMsg(
        `Retention done. Deleted ${r.audit?.deleted ?? 0} audit rows, ${r.chats?.deleted ?? 0} chats.`,
      );
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Retention failed");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Audit & Usage</h1>
          <p className="text-sm text-muted-foreground">Activity log + month-to-date token cost.</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={load}>
            <RefreshCw className="h-3.5 w-3.5" /> Refresh
          </Button>
          <Button variant="outline" size="sm" onClick={runRetention} disabled={running}>
            {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
            Run retention
          </Button>
        </div>
      </div>

      {error && <Alert variant="destructive">{error}</Alert>}
      {okMsg && <Alert variant="success">{okMsg}</Alert>}

      {/* Cost dashboard */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">💸 Month-to-date</CardTitle>
        </CardHeader>
        <CardContent>
          {usage === null ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
                <Stat label="Cost (USD)" value={`$${usage.total.cost_usd.toFixed(4)}`} />
                <Stat label="Input tokens" value={usage.total.input_tokens.toLocaleString()} />
                <Stat label="Output tokens" value={usage.total.output_tokens.toLocaleString()} />
                <Stat
                  label={usage.total.budget > 0 ? "Budget used" : "Budget"}
                  value={
                    usage.total.budget > 0
                      ? `${usage.total.budget_pct?.toFixed(1) ?? "0"}%`
                      : "unlimited"
                  }
                />
              </div>
              {usage.users.length === 0 ? (
                <p className="text-sm text-muted-foreground">No queries this month.</p>
              ) : (
                <div className="border rounded">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/40 text-xs uppercase text-muted-foreground">
                      <tr>
                        <th className="text-left font-medium px-3 py-2">User</th>
                        <th className="text-right font-medium px-3 py-2">Calls</th>
                        <th className="text-right font-medium px-3 py-2">Input</th>
                        <th className="text-right font-medium px-3 py-2">Output</th>
                        <th className="text-right font-medium px-3 py-2">Cached</th>
                        <th className="text-right font-medium px-3 py-2">Cost</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y">
                      {usage.users.map((u) => (
                        <tr key={u.user}>
                          <td className="px-3 py-2 font-medium">{u.user}</td>
                          <td className="px-3 py-2 text-right">{u.calls}</td>
                          <td className="px-3 py-2 text-right">{u.input_tokens.toLocaleString()}</td>
                          <td className="px-3 py-2 text-right">{u.output_tokens.toLocaleString()}</td>
                          <td className="px-3 py-2 text-right">{u.cached_tokens.toLocaleString()}</td>
                          <td className="px-3 py-2 text-right font-medium">${u.cost_usd.toFixed(4)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="text-xs text-muted-foreground mt-2">
                Window: {fmt(usage.month_start)} → {fmt(usage.month_end)}
              </p>
            </>
          )}
        </CardContent>
      </Card>

      {/* Audit log */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">📜 Recent activity</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {audit === null ? (
            <p className="px-6 py-8 text-sm text-muted-foreground">Loading…</p>
          ) : audit.length === 0 ? (
            <p className="px-6 py-8 text-sm text-muted-foreground">No activity yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="bg-muted/40 uppercase text-muted-foreground">
                  <tr>
                    <th className="text-left font-medium px-3 py-2">Time</th>
                    <th className="text-left font-medium px-3 py-2">User</th>
                    <th className="text-left font-medium px-3 py-2">Event</th>
                    <th className="text-left font-medium px-3 py-2">Status</th>
                    <th className="text-right font-medium px-3 py-2">Latency</th>
                    <th className="text-right font-medium px-3 py-2">Tokens (in/out/cached)</th>
                    <th className="text-left font-medium px-3 py-2">Question / Error</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {audit.map((row, i) => (
                    <tr key={i}>
                      <td className="px-3 py-1.5 whitespace-nowrap text-muted-foreground">{fmt(row.ts)}</td>
                      <td className="px-3 py-1.5 whitespace-nowrap">{row.user}</td>
                      <td className="px-3 py-1.5 whitespace-nowrap">{row.event}</td>
                      <td className="px-3 py-1.5">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] uppercase ${
                            row.status === "ok"
                              ? "bg-green-100 text-green-800"
                              : row.status === "error"
                              ? "bg-destructive/10 text-destructive"
                              : "bg-muted text-muted-foreground"
                          }`}
                        >
                          {row.status || "—"}
                        </span>
                      </td>
                      <td className="px-3 py-1.5 text-right text-muted-foreground">
                        {row.latency_ms ? `${row.latency_ms} ms` : "—"}
                      </td>
                      <td className="px-3 py-1.5 text-right text-muted-foreground whitespace-nowrap">
                        {row.input_tokens || row.output_tokens || row.cached_tokens
                          ? `${row.input_tokens ?? 0} / ${row.output_tokens ?? 0} / ${row.cached_tokens ?? 0}`
                          : "—"}
                      </td>
                      <td className="px-3 py-1.5 max-w-md truncate text-muted-foreground" title={row.question || row.error || ""}>
                        {row.question || row.error || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="border rounded p-3">
      <p className="text-xs text-muted-foreground uppercase tracking-wide">{label}</p>
      <p className="text-lg font-semibold mt-0.5 break-all">{value}</p>
    </div>
  );
}

function fmt(iso?: string) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}
