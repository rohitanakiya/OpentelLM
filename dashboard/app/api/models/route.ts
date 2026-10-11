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

// Computed from raw events, not the per-minute rollup. The rollup only keeps
// per-minute percentiles, and percentiles can't be combined: the old
// "avg of minute p50s" and "max of minute p99s" were biased (one slow minute
// with 2 calls set the p99 for the whole week). A scan of llm_events for one
// tenant and date range is cheap, because the table is ordered by
// (tenant_id, project_id, timestamp).
const QUERY = `
  SELECT
    model,
    count()                                   AS request_count,
    sum(cost_usd)                             AS cost_usd,
    avg(latency_ms)                           AS avg_latency_ms,
    quantileTDigest(0.99)(latency_ms)         AS p99_latency_ms,
    countIf(status != 'success') / count()    AS error_rate
  FROM opentelm.llm_events
  WHERE tenant_id = {tenant:String}
    AND timestamp >= parseDateTime64BestEffort({fromIso:String}, 3, 'UTC')
    AND timestamp <  parseDateTime64BestEffort({toIso:String}, 3, 'UTC')
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
