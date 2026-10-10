"use client";

import { CheckCircle2, AlertTriangle, AlertOctagon } from "lucide-react";
import { useEffect, useState } from "react";
import { fmtCost, fmtMs, fmtPct, fmtRelative } from "@/lib/format";
import { CardListSkeleton } from "./skeletons";
import { ErrorState, EmptyState } from "./error-state";

type Alert = {
  type: "cost_spike" | "prompt_regression";
  severity: "warning" | "critical";
  detected_at: string;
  details: Record<string, unknown>;
  project_id: string;
};

const POLL_MS = 10_000;

export function AlertFeed() {
  const [alerts, setAlerts] = useState<Alert[] | null>(null);
  const [error, setError]   = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const r = await fetch("/api/alerts", { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setAlerts(await r.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load alerts");
    }
  }

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, []);

  if (error)        return <ErrorState message={error} onRetry={load} />;
  if (!alerts)     return <CardListSkeleton />;
  if (alerts.length === 0) {
    return (
      <EmptyState
        icon={CheckCircle2}
        title="No anomalies detected"
        description="Your LLMs are behaving."
      />
    );
  }

  return (
    <div className="space-y-3">
      {alerts.map((a, i) => (
        <AlertCard key={`${a.detected_at}-${i}`} alert={a} />
      ))}
    </div>
  );
}

function AlertCard({ alert }: { alert: Alert }) {
  const Icon = alert.severity === "critical" ? AlertOctagon : AlertTriangle;
  const severityColor =
    alert.severity === "critical"
      ? "text-danger bg-danger/15 border-danger/30"
      : "text-warning bg-warning/15 border-warning/30";

  const title =
    alert.type === "cost_spike" ? "Cost spike detected" : "Prompt regression";

  return (
    <div className="gradient-border p-4">
      <div className="flex items-start gap-3">
        <Icon size={20} className={alert.severity === "critical" ? "text-danger" : "text-warning"} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium">{title}</span>
            <span className={`text-xs px-2 py-0.5 rounded border ${severityColor}`}>
              {alert.severity}
            </span>
            <span className="text-xs text-muted">{fmtRelative(alert.detected_at)}</span>
          </div>
          <div className="text-sm text-muted mt-1 truncate">
            project <span className="font-mono">{alert.project_id}</span>
          </div>
          <AlertDetails alert={alert} />
        </div>
      </div>
    </div>
  );
}

function AlertDetails({ alert }: { alert: Alert }) {
  if (alert.type === "cost_spike") {
    const d = alert.details as {
      current_hour_cost_usd?: number;
      baseline_hourly_usd?: number;
      ema_baseline_usd?: number; // alerts stored before the hourly-baseline detector
      multiplier?: number;
    };
    return (
      <div className="text-sm mt-2 grid grid-cols-3 gap-4 max-w-md">
        <Detail label="this hour"   value={fmtCost(d.current_hour_cost_usd ?? 0)} />
        <Detail label="usual hour"  value={fmtCost(d.baseline_hourly_usd ?? d.ema_baseline_usd ?? 0)} />
        <Detail label="multiplier"  value={`${(d.multiplier ?? 0).toFixed(1)}×`} />
      </div>
    );
  }
  const d = alert.details as {
    metric?: "error_rate" | "latency"; // absent on alerts from the old detector
    current_version?: string;
    previous_version?: string;
    baseline_value?: number;
    current_value?: number;
    p_value?: number;
    error_rate_delta?: number;
    latency_delta_pct?: number;
  };
  const change =
    d.metric === "latency"
      ? `${fmtMs(d.baseline_value ?? 0)} → ${fmtMs(d.current_value ?? 0)} median`
      : d.metric === "error_rate"
        ? `${fmtPct(d.baseline_value ?? 0)} → ${fmtPct(d.current_value ?? 0)} errors`
        : null;
  return (
    <div className="text-sm mt-2 grid grid-cols-2 gap-4 max-w-md">
      <Detail label="versions"   value={`${d.previous_version ?? "?"} → ${d.current_version ?? "?"}`} />
      {change ? (
        <>
          <Detail label={d.metric === "latency" ? "latency" : "error rate"} value={change} />
          <Detail label="p-value" value={fmtPValue(d.p_value)} />
        </>
      ) : (
        <>
          <Detail label="error Δ"    value={fmtPct(d.error_rate_delta ?? 0)} />
          <Detail label="latency Δ"  value={fmtPct(d.latency_delta_pct ?? 0)} />
        </>
      )}
    </div>
  );
}

function fmtPValue(p?: number): string {
  if (p === undefined) return "–";
  return p < 0.0001 ? "< 0.0001" : p.toPrecision(2);
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wider text-muted">{label}</div>
      <div className="tabular-nums">{value}</div>
    </div>
  );
}
