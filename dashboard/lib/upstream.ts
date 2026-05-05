// Helper for proxying Bearer-authenticated requests from the dashboard
// to auth-service. The browser holds an HttpOnly cookie; we forward the
// JWT inside the Authorization header so auth-service sees a normal
// Bearer token request and never has to know about cookies.

import { NextRequest, NextResponse } from "next/server";
import { ACCESS_COOKIE } from "@/lib/auth";
import { env } from "@/lib/env";

export async function proxyToAuth(
  req: NextRequest,
  path: string,
  init: { method?: string; body?: unknown } = {},
): Promise<NextResponse> {
  const access = req.cookies.get(ACCESS_COOKIE)?.value;
  if (!access) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  const upstream = await fetch(`${env.AUTH_SERVICE_URL}${path}`, {
    method: init.method ?? "GET",
    headers: {
      Authorization: `Bearer ${access}`,
      "Content-Type": "application/json",
    },
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    cache: "no-store",
  });

  const text = await upstream.text();
  // Pass status through verbatim so the dashboard can react to 404, 409, etc.
  return new NextResponse(text || null, {
    status: upstream.status,
    headers: { "Content-Type": "application/json" },
  });
}
