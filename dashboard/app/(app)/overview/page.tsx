"use client";

import { useEffect, useState } from "react";
import { LiveChart } from "@/components/live-chart";
import { StatCard } from "@/components/stat-card";
import { StatCardSkeleton } from "@/components/skeletons";
import { ErrorState } from "@/components/error-state";
import { fmtCost, fmtMs, fmtPct, fmtTokens } from "@/lib/format";

type Stats = {
  requests_today: number;
  cost_today: number;
  p99_latency: number;
  error_rate: number;
};

export default function OverviewPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const r = await fetch("/api/overview/stats", { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setStats(await r.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load stats");
    }
  }

  useEffect(() => { load(); }, []);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Overview</h1>
        <p className="text-sm text-muted">Today&apos;s aggregate metrics and live traffic.</p>
      </header>

      {error ? (
        <ErrorState message={error} onRetry={load} />
      ) : !stats ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCardSkeleton /><StatCardSkeleton /><StatCardSkeleton /><StatCardSkeleton />
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard label="Requests today" value={fmtTokens(stats.requests_today)} />
          <StatCard label="Cost today"     value={fmtCost(stats.cost_today)} />
          <StatCard label="p99 latency" value={fmtMs(stats.p99_latency)} />
          <StatCard
            label="Error rate"
            value={fmtPct(stats.error_rate)}
            trend={stats.error_rate > 0.05 ? "down" : "up"}
          />
        </div>
      )}

      <LiveChart />
    </div>
  );
}
