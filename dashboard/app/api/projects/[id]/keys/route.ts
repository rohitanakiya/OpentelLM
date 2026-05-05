import { NextRequest } from "next/server";
import { proxyToAuth } from "@/lib/upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest, ctx: { params: { id: string } }) {
  return proxyToAuth(req, `/projects/${ctx.params.id}/keys`, { method: "GET" });
}

export async function POST(req: NextRequest, ctx: { params: { id: string } }) {
  const body = await req.json().catch(() => ({}));
  return proxyToAuth(req, `/projects/${ctx.params.id}/keys`, { method: "POST", body });
}
