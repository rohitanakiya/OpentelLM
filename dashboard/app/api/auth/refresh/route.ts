import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { ACCESS_COOKIE, REFRESH_COOKIE } from "@/lib/auth";
import { env } from "@/lib/env";

// POST /api/auth/refresh — used by the dashboard if it ever needs to
// trigger a refresh manually. Middleware does most refreshes silently.
export async function POST() {
  const refresh = cookies().get(REFRESH_COOKIE)?.value;
  if (!refresh) return NextResponse.json({ error: "no_refresh_cookie" }, { status: 401 });

  const upstream = await fetch(`${env.AUTH_SERVICE_URL}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
    cache: "no-store",
  });
  if (!upstream.ok) {
    return NextResponse.json({ error: "refresh_failed" }, { status: 401 });
  }
  const data: { access_token: string; expires_in: number } = await upstream.json();
  const res = NextResponse.json({ ok: true });
  res.cookies.set(ACCESS_COOKIE, data.access_token, {
    httpOnly: true, sameSite: "strict", secure: env.COOKIE_SECURE,
    path: "/", maxAge: data.expires_in,
  });
  return res;
}
