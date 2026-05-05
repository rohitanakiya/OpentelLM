import { NextResponse } from "next/server";
import { REFRESH_COOKIE, clearAuthCookies } from "@/lib/auth";
import { cookies } from "next/headers";
import { env } from "@/lib/env";

export async function POST() {
  const refresh = cookies().get(REFRESH_COOKIE)?.value;

  // Best-effort upstream logout: invalidates the refresh token in Redis.
  // We still clear local cookies even if the upstream call fails.
  if (refresh) {
    try {
      await fetch(`${env.AUTH_SERVICE_URL}/auth/logout`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
        cache: "no-store",
      });
    } catch {
      // ignore
    }
  }

  return clearAuthCookies(NextResponse.json({ ok: true }));
}
