import { NextResponse } from "next/server";
import { getCurrentTenant, unauthorized } from "@/lib/auth";
import { chQuery } from "@/lib/clickhouse";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Per-minute requests for the last 30 minutes. We always fill missing
// minutes with zeros so the chart renders a continuous line even when the
// app has been idle.
const QUERY = `
  SELECT
    toStartOfMinute(window_start) AS minute,
    sum(request_count)            AS request_count
  FROM opentelm.llm_metrics_1m
  WHERE tenant_id = {tenant:String}
    AND window_start >= now() - INTERVAL 30 MINUTE
  GROUP BY minute
  ORDER BY minute ASC
`;

type Row = { minute: string; request_count: number };

export async function GET() {
  const claims = await getCurrentTenant();
  if (!claims) return unauthorized();

  let rows: Row[];
  try {
    rows = await chQuery<Row>(QUERY, { tenant: claims.tenant_id });
  } catch (err) {
    console.error("timeseries query failed", err);
    return NextResponse.json({ error: "query_failed" }, { status: 502 });
  }

  // Build a 30-minute window and zero-fill gaps.
  const now = new Date();
  now.setSeconds(0, 0);
  const byMinute = new Map(rows.map((r) => [
    new Date(r.minute + "Z").toISOString(),
    Number(r.request_count) || 0,
  ]));
  const series: { minute: string; request_count: number }[] = [];
  for (let i = 29; i >= 0; i--) {
    const t = new Date(now.getTime() - i * 60_000);
    const iso = t.toISOString();
    series.push({ minute: iso, request_count: byMinute.get(iso) ?? 0 });
  }
  return NextResponse.json(series);
}
