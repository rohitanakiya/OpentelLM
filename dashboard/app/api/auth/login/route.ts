import { NextResponse } from "next/server";
import { setAuthCookies } from "@/lib/auth";
import { env } from "@/lib/env";

// POST /api/auth/login
// { email, password } → forwards to auth-service /auth/login, then sets
// the access + refresh tokens as HttpOnly cookies. The browser never
// receives the tokens in JSON.
export async function POST(req: Request) {
  let body: { email?: string; password?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }

  if (!body.email || !body.password) {
    return NextResponse.json({ error: "missing_credentials" }, { status: 400 });
  }

  const upstream = await fetch(`${env.AUTH_SERVICE_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: body.email, password: body.password }),
    cache: "no-store",
  });

  if (!upstream.ok) {
    // Bubble up the auth-service status (typically 401)
    const detail = await upstream.json().catch(() => ({ error: "login_failed" }));
    return NextResponse.json(detail, { status: upstream.status });
  }

  const tokens: {
    access_token: string;
    refresh_token: string;
    expires_in: number;
  } = await upstream.json();

  const res = NextResponse.json({ ok: true });
  return setAuthCookies(res, tokens.access_token, tokens.refresh_token, tokens.expires_in);
}
