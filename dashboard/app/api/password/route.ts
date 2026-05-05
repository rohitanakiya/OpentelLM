import { NextRequest } from "next/server";
import { proxyToAuth } from "@/lib/upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// PATCH /api/password — proxies to auth-service PATCH /auth/password.
// Auth-service invalidates every refresh token for the tenant, so the
// browser will need to re-login. We don't preemptively clear cookies
// here — the user can explicitly log out.
export async function PATCH(req: NextRequest) {
  const body = await req.json().catch(() => ({}));
  return proxyToAuth(req, "/auth/password", { method: "PATCH", body });
}
