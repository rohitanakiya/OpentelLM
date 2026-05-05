"use client";

import { ChevronDown, FileText } from "lucide-react";
import { useEffect, useState, useTransition } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { fmtCost, fmtDate, fmtMs, fmtTokens } from "@/lib/format";
import { TableSkeleton } from "./skeletons";
import { ErrorState, EmptyState } from "./error-state";

type Trace = {
  timestamp: string;
  trace_id: string;
  model: string;
  provider: string;
  status: string;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  latency_ms: number;
  prompt_hash: string;
  response_hash: string;
};

type Page = { rows: Trace[]; next_cursor: string | null };

const STATUS_BADGE: Record<string, string> = {
  success: "bg-success/15 text-success border-success/30",
  error:   "bg-danger/15 text-danger border-danger/30",
  timeout: "bg-warning/15 text-warning border-warning/30",
};

export function TracesTable() {
  const router = useRouter();
  const params = useSearchParams();

  const model      = params.get("model") ?? "";
  const status     = params.get("status") ?? "";
  const from       = params.get("from") ?? "";
  const to         = params.get("to") ?? "";
  const minLatency = params.get("min_latency") ?? "";

  const [data, setData]    = useState<Page | null>(null);
  const [error, setError]  = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [, startTransition] = useTransition();

  function buildQS(extras: Record<string, string | null> = {}): string {
    const sp = new URLSearchParams();
    const merged = { model, status, from, to, min_latency: minLatency, ...extras };
    for (const [k, v] of Object.entries(merged)) if (v) sp.set(k, v);
    return sp.toString();
  }

  async function load(cursor: string | null = null) {
    setError(null);
    try {
      const sp = new URLSearchParams();
      if (model)      sp.set("model", model);
      if (status)     sp.set("status", status);
      if (from)       sp.set("from", from);
      if (to)         sp.set("to", to);
      if (minLatency) sp.set("min_latency", minLatency);
      if (cursor)     sp.set("cursor", cursor);
      const r = await fetch(`/api/traces?${sp.toString()}`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load traces");
    }
  }

  useEffect(() => { load(null); }, [model, status, from, to, minLatency]);

  function setFilter(key: string, value: string) {
    startTransition(() => {
      router.replace(`/traces?${buildQS({ [key]: value || null })}`);
    });
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
        <input
          placeholder="model contains…"
          value={model}
          onChange={(e) => setFilter("model", e.target.value)}
          className="bg-surface border border-border rounded-md px-3 py-2 text-sm focus:outline-none focus:border-accent"
        />
        <select
          value={status}
          onChange={(e) => setFilter("status", e.target.value)}
          className="bg-surface border border-border rounded-md px-3 py-2 text-sm focus:outline-none focus:border-accent"
        >
          <option value="">all statuses</option>
          <option value="success">success</option>
          <option value="error">error</option>
          <option value="timeout">timeout</option>
        </select>
        <input
          type="datetime-local"
          value={from}
          onChange={(e) => setFilter("from", e.target.value)}
          className="bg-surface border border-border rounded-md px-3 py-2 text-sm focus:outline-none focus:border-accent"
        />
        <input
          type="datetime-local"
          value={to}
          onChange={(e) => setFilter("to", e.target.value)}
          className="bg-surface border border-border rounded-md px-3 py-2 text-sm focus:outline-none focus:border-accent"
        />
        <input
          type="number"
          min="0"
          placeholder="min latency (ms)"
          value={minLatency}
          onChange={(e) => setFilter("min_latency", e.target.value)}
          className="bg-surface border border-border rounded-md px-3 py-2 text-sm focus:outline-none focus:border-accent"
        />
      </div>

      {error ? (
        <ErrorState message={error} onRetry={() => load(null)} />
      ) : !data ? (
        <TableSkeleton />
      ) : data.rows.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="No traces match these filters"
          description="Try widening your date range or clearing filters."
        />
      ) : (
        <div className="gradient-border overflow-hidden">
          <table className="w-full text-sm">
            <thead className="text-xs uppercase text-muted bg-surface2/40">
              <tr>
                <th className="text-left px-4 py-2.5">Timestamp</th>
                <th className="text-left px-4 py-2.5">Model</th>
                <th className="text-left px-4 py-2.5">Provider</th>
                <th className="text-left px-4 py-2.5">Status</th>
                <th className="text-right px-4 py-2.5">In</th>
                <th className="text-right px-4 py-2.5">Out</th>
                <th className="text-right px-4 py-2.5">Cost</th>
                <th className="text-right px-4 py-2.5">Latency</th>
                <th className="w-8" />
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row) => {
                const open = expanded === row.trace_id;
                return (
                  <>
                    <tr
                      key={row.trace_id}
                      onClick={() => setExpanded(open ? null : row.trace_id)}
                      className="border-t border-border hover:bg-surface2/30 cursor-pointer"
                    >
                      <td className="px-4 py-2.5 tabular-nums">{fmtDate(row.timestamp)}</td>
                      <td className="px-4 py-2.5 font-mono text-xs">{row.model}</td>
                      <td className="px-4 py-2.5 text-muted">{row.provider}</td>
                      <td className="px-4 py-2.5">
                        <span className={`inline-block text-xs px-2 py-0.5 rounded border ${STATUS_BADGE[row.status] ?? ""}`}>
                          {row.status}
                        </span>
                      </td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{fmtTokens(row.prompt_tokens)}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{fmtTokens(row.completion_tokens)}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{fmtCost(row.cost_usd)}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{fmtMs(row.latency_ms)}</td>
                      <td className="px-2">
                        <ChevronDown
                          size={16}
                          className={`text-muted transition-transform ${open ? "rotate-180" : ""}`}
                        />
                      </td>
                    </tr>
                    {open ? (
                      <tr className="bg-bg/50 border-t border-border">
                        <td colSpan={9} className="px-4 py-3">
                          <dl className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
                            <Detail label="Trace ID"      value={row.trace_id} mono />
                            <Detail label="Prompt hash"   value={row.prompt_hash} mono />
                            <Detail label="Response hash" value={row.response_hash} mono />
                          </dl>
                        </td>
                      </tr>
                    ) : null}
                  </>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {data?.next_cursor ? (
        <div className="flex justify-end">
          <button
            onClick={() => load(data.next_cursor)}
            className="px-4 py-2 text-sm border border-border rounded-md hover:bg-surface2 transition-colors"
          >
            Load more
          </button>
        </div>
      ) : null}
    </div>
  );
}

function Detail({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-muted uppercase tracking-wider mb-1">{label}</dt>
      <dd className={`break-all ${mono ? "font-mono" : ""}`}>{value}</dd>
    </div>
  );
}
