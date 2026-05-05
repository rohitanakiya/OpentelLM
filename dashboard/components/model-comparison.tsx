"use client";

import { Layers } from "lucide-react";
import { useEffect, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { fmtCost, fmtMs, fmtPct, fmtTokens } from "@/lib/format";
import { ChartSkeleton, TableSkeleton } from "./skeletons";
import { ErrorState, EmptyState } from "./error-state";

type ModelStat = {
  model: string;
  request_count: number;
  cost_usd: number;
  avg_latency_ms: number;
  p99_latency_ms: number;
  error_rate: number;
};

function defaultRange(): { from: string; to: string } {
  const now = new Date();
  const past = new Date(Date.now() - 7 * 86_400_000);
  // datetime-local input format: YYYY-MM-DDTHH:mm
  const fmt = (d: Date) => d.toISOString().slice(0, 16);
  return { from: fmt(past), to: fmt(now) };
}

export function ModelComparison() {
  const def = defaultRange();
  const [from, setFrom]   = useState(def.from);
  const [to, setTo]       = useState(def.to);
  const [stats, setStats] = useState<ModelStat[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const sp = new URLSearchParams({ from, to });
      const r = await fetch(`/api/models?${sp.toString()}`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setStats(await r.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load model stats");
    }
  }

  useEffect(() => { load(); }, [from, to]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-3 items-end">
        <DateField label="From" value={from} onChange={setFrom} />
        <DateField label="To"   value={to}   onChange={setTo} />
      </div>

      {error ? (
        <ErrorState message={error} onRetry={load} />
      ) : !stats ? (
        <>
          <ChartSkeleton />
          <TableSkeleton rows={6} />
        </>
      ) : stats.length === 0 ? (
        <EmptyState
          icon={Layers}
          title="No model data yet"
          description="Make some LLM calls and they'll appear here."
        />
      ) : (
        <>
          <div className="gradient-border p-5">
            <h3 className="text-sm font-medium mb-4">Cost vs avg latency</h3>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={stats} margin={{ top: 6, right: 12, bottom: 0, left: -10 }}>
                <CartesianGrid stroke="#2a2d44" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="model" stroke="#8b8fae" fontSize={11} />
                <YAxis yAxisId="left"  stroke="#8b8fae" fontSize={11} />
                <YAxis yAxisId="right" orientation="right" stroke="#8b8fae" fontSize={11} />
                <Tooltip
                  contentStyle={{ background: "#1a1d2e", border: "1px solid #2a2d44", borderRadius: 8 }}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar yAxisId="left"  dataKey="cost_usd"        name="cost (USD)"     fill="#7c3aed" radius={[4,4,0,0]} />
                <Bar yAxisId="right" dataKey="avg_latency_ms"  name="avg latency ms" fill="#0ea5e9" radius={[4,4,0,0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="gradient-border overflow-hidden">
            <table className="w-full text-sm">
              <thead className="text-xs uppercase text-muted bg-surface2/40">
                <tr>
                  <th className="text-left px-4 py-2.5">Model</th>
                  <th className="text-right px-4 py-2.5">Requests</th>
                  <th className="text-right px-4 py-2.5">Cost</th>
                  <th className="text-right px-4 py-2.5">Avg latency</th>
                  <th className="text-right px-4 py-2.5">p99 latency</th>
                  <th className="text-right px-4 py-2.5">Error rate</th>
                </tr>
              </thead>
              <tbody>
                {stats.map((m) => (
                  <tr key={m.model} className="border-t border-border">
                    <td className="px-4 py-2.5 font-mono text-xs">{m.model}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{fmtTokens(m.request_count)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{fmtCost(m.cost_usd)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{fmtMs(m.avg_latency_ms)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{fmtMs(m.p99_latency_ms)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{fmtPct(m.error_rate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function DateField({ label, value, onChange }: { label: string; value: string; onChange: (s: string) => void }) {
  return (
    <div>
      <label className="block text-xs uppercase tracking-wider text-muted mb-1">{label}</label>
      <input
        type="datetime-local"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="bg-surface border border-border rounded-md px-3 py-2 text-sm focus:outline-none focus:border-accent"
      />
    </div>
  );
}
