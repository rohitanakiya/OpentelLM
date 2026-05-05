import { NextResponse } from "next/server";
import { getCurrentTenant, unauthorized } from "@/lib/auth";
import { chQuery } from "@/lib/clickhouse";

// Force Node.js runtime — @clickhouse/client doesn't run on Edge.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Row = {
  requests_today: number;
  cost_today: number;
  avg_p99_latency: number;
  error_rate: number;
};

// Single ClickHouse query against the pre-aggregated metrics table.
// Querying raw events for "today's stats" would be 100x slower at scale
// — that's the whole point of llm_metrics_1m.
const QUERY = `
  SELECT
    sum(request_count)                                              AS requests_today,
    sum(cost_usd)                                                   AS cost_today,
    avg(p99_latency_ms)                                             AS avg_p99_latency,
    if(sum(request_count) > 0,
       sum(error_count) / sum(request_count),
       0)                                                           AS error_rate
  FROM opentelm.llm_metrics_1m
  WHERE tenant_id = {tenant:String}
    AND window_start >= toStartOfDay(now())
`;

export async function GET() {
  const claims = await getCurrentTenant();
  if (!claims) return unauthorized();

  try {
    const rows = await chQuery<Row>(QUERY, { tenant: claims.tenant_id });
    const r = rows[0] ?? {
      requests_today: 0, cost_today: 0, avg_p99_latency: 0, error_rate: 0,
    };
    return NextResponse.json({
      requests_today:  Number(r.requests_today)  || 0,
      cost_today:      Number(r.cost_today)      || 0,
      avg_p99_latency: Number(r.avg_p99_latency) || 0,
      error_rate:      Number(r.error_rate)      || 0,
    });
  } catch (err) {
    console.error("stats query failed", err);
    return NextResponse.json({ error: "query_failed" }, { status: 502 });
  }
}
