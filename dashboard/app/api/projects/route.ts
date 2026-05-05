import { NextRequest } from "next/server";
import { proxyToAuth } from "@/lib/upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  return proxyToAuth(req, "/projects", { method: "GET" });
}

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => ({}));
  return proxyToAuth(req, "/projects", { method: "POST", body });
}
