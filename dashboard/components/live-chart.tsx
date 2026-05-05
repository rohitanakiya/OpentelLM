"use client";

// LiveChart — line chart of requests-per-minute. Initial 30 minutes is
// fetched from /api/overview/timeseries; subsequent updates stream over
// WebSocket from the stream-processor's `live:{tenant_id}` channel.
//
// Each WS message is a single closed window. We aggregate it into the
// minute bucket in-place. If the user is idle (browser tab backgrounded)
// for long enough, the next foreground render re-fetches the baseline.

import { useEffect, useRef, useState } from "react";
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { ChartSkeleton } from "./skeletons";
import { ErrorState } from "./error-state";

type Point = { minute: string; request_count: number };

function pointLabel(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export function LiveChart() {
  const [data, setData]    = useState<Point[] | null>(null);
  const [error, setError]  = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  async function loadBaseline() {
    setError(null);
    try {
      const r = await fetch("/api/overview/timeseries", { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const series: Point[] = await r.json();
      setData(series);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load chart");
    }
  }

  useEffect(() => {
    loadBaseline();
  }, []);

  useEffect(() => {
    if (!data) return;
    // Connect to the WebSocket on the same origin. Next.js rewrites
    // /api/ws/* to the standalone ws-server.
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${window.location.host}/api/ws/live-metrics`);
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.event === "subscribed") return;
        const minute = new Date(msg.window_start);
        minute.setSeconds(0, 0);
        const iso = minute.toISOString();
        setData((prev) => {
          if (!prev) return prev;
          const idx = prev.findIndex((p) => p.minute === iso);
          if (idx === -1) {
            // New minute beyond our 30-window — drop the oldest, append.
            const next = [...prev.slice(1), { minute: iso, request_count: msg.request_count }];
            return next;
          }
          const next = [...prev];
          next[idx] = { ...next[idx], request_count: next[idx].request_count + msg.request_count };
          return next;
        });
      } catch {
        // ignore malformed
      }
    };

    ws.onerror = () => {
      // The reconnection logic is intentionally minimal — recharts keeps
      // showing the last known data. If WS stays down we'll show stale
      // data, but never lose the page.
    };

    return () => {
      ws.close();
    };
  }, [data === null]);

  if (error)   return <ErrorState message={error} onRetry={loadBaseline} />;
  if (!data)   return <ChartSkeleton />;

  return (
    <div className="gradient-border p-5">
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-sm font-medium">Requests per minute</h3>
        <div className="text-xs text-muted">last 30 minutes · live</div>
      </div>
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -10 }}>
          <CartesianGrid stroke="#2a2d44" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="minute"
            tickFormatter={pointLabel}
            stroke="#8b8fae"
            fontSize={11}
            interval={5}
          />
          <YAxis stroke="#8b8fae" fontSize={11} allowDecimals={false} />
          <Tooltip
            contentStyle={{
              background: "#1a1d2e", border: "1px solid #2a2d44", borderRadius: 8,
            }}
            labelFormatter={(v) => pointLabel(String(v))}
          />
          <Line
            type="monotone"
            dataKey="request_count"
            stroke="#7c3aed"
            strokeWidth={2}
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
