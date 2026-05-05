import { NextResponse } from "next/server";
import { getCurrentTenant, unauthorized } from "@/lib/auth";
import { chQuery } from "@/lib/clickhouse";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const PAGE_SIZE = 50;

type TraceRow = {
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

// Cursor encodes the last row's (timestamp, trace_id) so the next page
// resumes deterministically. Offset pagination would skew under
// concurrent inserts and gets slower with depth — cursors are O(1).
function encodeCursor(timestamp: string, traceId: string): string {
  return Buffer.from(`${timestamp}|${traceId}`).toString("base64url");
}

function decodeCursor(c: string): { timestamp: string; traceId: string } | null {
  try {
    const [timestamp, traceId] = Buffer.from(c, "base64url").toString().split("|");
    if (!timestamp || !traceId) return null;
    return { timestamp, traceId };
  } catch {
    return null;
  }
}

export async function GET(req: Request) {
  const claims = await getCurrentTenant();
  if (!claims) return unauthorized();

  const url = new URL(req.url);
  const model       = url.searchParams.get("model") ?? "";
  const status      = url.searchParams.get("status") ?? "";
  const fromIso     = url.searchParams.get("from");
  const toIso       = url.searchParams.get("to");
  const minLatency  = Number(url.searchParams.get("min_latency") ?? "0") || 0;
  const cursorRaw   = url.searchParams.get("cursor");

  const cursor = cursorRaw ? decodeCursor(cursorRaw) : null;

  // Build the WHERE dynamically. We always parameterise — never interpolate.
  const where: string[] = ["tenant_id = {tenant:String}"];
  const params: Record<string, unknown> = { tenant: claims.tenant_id };

  if (model)  { where.push("model = {model:String}");  params.model  = model; }
  if (status) { where.push("status = {st:String}");    params.st     = status; }
  if (fromIso){ where.push("timestamp >= parseDateTime64BestEffort({fromIso:String})"); params.fromIso = fromIso; }
  if (toIso)  { where.push("timestamp <  parseDateTime64BestEffort({toIso:String})");   params.toIso   = toIso; }
  if (minLatency > 0) { where.push("latency_ms >= {minLat:UInt32}"); params.minLat = minLatency; }

  if (cursor) {
    // Strict tuple comparison: rows STRICTLY older than the cursor row.
    where.push(
      "(timestamp, trace_id) < (parseDateTime64BestEffort({curTs:String}), {curTrace:String})",
    );
    params.curTs    = cursor.timestamp;
    params.curTrace = cursor.traceId;
  }

  // Fetch PAGE_SIZE + 1 to know if there's a next page without a count(*).
  const query = `
    SELECT
      formatDateTime(timestamp, '%Y-%m-%dT%H:%i:%S.%fZ') AS timestamp,
      trace_id, model, provider, status,
      prompt_tokens, completion_tokens,
      cost_usd, latency_ms,
      prompt_hash, response_hash
    FROM opentelm.llm_events
    WHERE ${where.join(" AND ")}
    ORDER BY timestamp DESC, trace_id DESC
    LIMIT ${PAGE_SIZE + 1}
  `;

  let rows: TraceRow[];
  try {
    rows = await chQuery<TraceRow>(query, params);
  } catch (err) {
    console.error("traces query failed", err);
    return NextResponse.json({ error: "query_failed" }, { status: 502 });
  }

  let nextCursor: string | null = null;
  if (rows.length > PAGE_SIZE) {
    const last = rows[PAGE_SIZE - 1];
    nextCursor = encodeCursor(last.timestamp, last.trace_id);
    rows = rows.slice(0, PAGE_SIZE);
  }

  return NextResponse.json({ rows, next_cursor: nextCursor });
}
