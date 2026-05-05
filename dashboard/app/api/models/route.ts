import { NextResponse } from "next/server";
import { getCurrentTenant, unauthorized } from "@/lib/auth";
import { chQuery } from "@/lib/clickhouse";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Row = {
  model: string;
  request_count: number;
  cost_usd: number;
  avg_latency_ms: number;
  p99_latency_ms: number;
  error_rate: number;
};

const QUERY = `
  SELECT
    model,
    sum(request_count)                                       AS request_count,
    sum(cost_usd)                                            AS cost_usd,
    avg(p50_latency_ms)                                      AS avg_latency_ms,
    max(p99_latency_ms)                                      AS p99_latency_ms,
    if(sum(request_count) > 0,
       sum(error_count) / sum(request_count), 0)             AS error_rate
  FROM opentelm.llm_metrics_1m
  WHERE tenant_id = {tenant:String}
    AND window_start >= parseDateTime64BestEffort({fromIso:String})
    AND window_start <  parseDateTime64BestEffort({toIso:String})
  GROUP BY model
  ORDER BY request_count DESC
`;

export async function GET(req: Request) {
  const claims = await getCurrentTenant();
  if (!claims) return unauthorized();

  const url = new URL(req.url);
  const fromIso = url.searchParams.get("from") ??
    new Date(Date.now() - 7 * 86_400 * 1000).toISOString();
  const toIso = url.searchParams.get("to") ?? new Date().toISOString();

  try {
    const rows = await chQuery<Row>(QUERY, {
      tenant: claims.tenant_id, fromIso, toIso,
    });
    return NextResponse.json(rows.map((r) => ({
      model:           r.model,
      request_count:   Number(r.request_count) || 0,
      cost_usd:        Number(r.cost_usd) || 0,
      avg_latency_ms:  Number(r.avg_latency_ms) || 0,
      p99_latency_ms:  Number(r.p99_latency_ms) || 0,
      error_rate:      Number(r.error_rate) || 0,
    })));
  } catch (err) {
    console.error("models query failed", err);
    return NextResponse.json({ error: "query_failed" }, { status: 502 });
  }
}
