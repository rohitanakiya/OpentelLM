import { NextResponse } from "next/server";
import { getCurrentTenant, unauthorized } from "@/lib/auth";
import { redis } from "@/lib/redis";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Alert = {
  type: "cost_spike" | "prompt_regression";
  tenant_id: string;
  project_id: string;
  severity: "warning" | "critical";
  detected_at: string;
  details: Record<string, unknown>;
};

export async function GET() {
  const claims = await getCurrentTenant();
  if (!claims) return unauthorized();

  // anomaly-consumer LPUSHes most-recent first and LTRIMs to 100 — so
  // LRANGE 0 99 returns the alerts in newest→oldest order already.
  const raw = await redis().lrange(`alerts:${claims.tenant_id}`, 0, 99);
  const alerts: Alert[] = raw
    .map((s) => {
      try { return JSON.parse(s); } catch { return null; }
    })
    .filter((x): x is Alert => x !== null)
    // Defensive sort in case the list got out of order somehow
    .sort((a, b) => (a.detected_at < b.detected_at ? 1 : -1));

  return NextResponse.json(alerts);
}
