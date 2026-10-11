import { NextResponse } from "next/server";
import { getCurrentTenant, unauthorized } from "@/lib/auth";
import { chQuery } from "@/lib/clickhouse";

// Force Node.js runtime — @clickhouse/client doesn't run on Edge.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Row = {
  requests_today: number;
  cost_today: number;
  p99_latency: number;
  error_rate: number;
};

// One query over the pre-aggregated 1-minute table (cheap: ~1440 rows per
// model per day, vs every raw event).
//
// p99: percentiles can't be averaged, so each row stores its t-digest
// centroids and ClickHouse merges them into a true p99 for the whole day.
// (This used to be avg(p99_latency_ms): an average of per-minute p99s,
// which is not a p99 of anything.) A minute can have more than one row when
// events arrive late, so everything here is a sum or a merge, never a
// per-row value.
const QUERY = `
  SELECT
    sum(request_count)                                              AS requests_today,
    sum(cost_usd)                                                   AS cost_today,
    quantileTDigestWeightedArray(0.99)(latency_means, latency_weights) AS p99_latency,
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
      requests_today: 0, cost_today: 0, p99_latency: 0, error_rate: 0,
    };
    return NextResponse.json({
      requests_today:  Number(r.requests_today)  || 0,
      cost_today:      Number(r.cost_today)      || 0,
      p99_latency:     Number(r.p99_latency)     || 0,   // NaN (no data) -> 0
      error_rate:      Number(r.error_rate)      || 0,
    });
  } catch (err) {
    console.error("stats query failed", err);
    return NextResponse.json({ error: "query_failed" }, { status: 502 });
  }
}
