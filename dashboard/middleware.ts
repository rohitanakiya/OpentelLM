// Edge middleware: enforces authentication on every protected route.
//
// Behaviour:
//   - Page routes (/overview, /traces, /alerts, /models, /settings):
//       no/invalid token → 302 to /login
//   - API routes  (/api/* except /api/auth/login and /api/auth/refresh):
//       no/invalid token → 401 JSON
//   - If access token is valid but expires in < 60s, attempt a silent refresh
//     against auth-service /auth/refresh using the refresh cookie. On
//     success, set a new access cookie on the response. On failure, clear
//     cookies and bounce to /login (for pages) or 401 (for API).
//
// Note: jose works in the Edge runtime; ioredis and @clickhouse/client do not.

import { NextResponse, type NextRequest } from "next/server";
import {
  ACCESS_COOKIE,
  REFRESH_COOKIE,
  clearAuthCookies,
  setAuthCookies,
  verifyAccess,
} from "@/lib/auth";
import { env } from "@/lib/env";

const PROTECTED_PAGES = ["/overview", "/traces", "/alerts", "/models", "/settings"];
const OPEN_API_PATHS  = ["/api/auth/login", "/api/auth/refresh", "/api/auth/logout"];

function isProtectedPage(pathname: string): boolean {
  return PROTECTED_PAGES.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

function isProtectedApi(pathname: string): boolean {
  return pathname.startsWith("/api/") && !OPEN_API_PATHS.some((p) => pathname.startsWith(p));
}

async function tryRefresh(refreshToken: string): Promise<{
  access_token: string;
  expires_in: number;
} | null> {
  try {
    const r = await fetch(`${env.AUTH_SERVICE_URL}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
      // Edge fetch must be cache:no-store
      cache: "no-store",
    });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

export async function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  if (!isProtectedPage(pathname) && !isProtectedApi(pathname)) {
    return NextResponse.next();
  }

  const access = req.cookies.get(ACCESS_COOKIE)?.value;
  const refresh = req.cookies.get(REFRESH_COOKIE)?.value;

  let claims = access ? await verifyAccess(access) : null;

  // Token absent or invalid — try to silently refresh
  if (!claims && refresh) {
    const refreshed = await tryRefresh(refresh);
    if (refreshed) {
      const res = NextResponse.next();
      // We don't have the refresh token's expiry exposed; reuse existing cookie.
      res.cookies.set(ACCESS_COOKIE, refreshed.access_token, {
        httpOnly: true,
        sameSite: "strict",
        secure: env.IS_PRODUCTION,
        path: "/",
        maxAge: refreshed.expires_in,
      });
      return res;
    }
  }

  // Proactive refresh: token still valid but expiring within 60s
  if (claims && claims.exp - Math.floor(Date.now() / 1000) < 60 && refresh) {
    const refreshed = await tryRefresh(refresh);
    if (refreshed) {
      const res = NextResponse.next();
      res.cookies.set(ACCESS_COOKIE, refreshed.access_token, {
        httpOnly: true,
        sameSite: "strict",
        secure: env.IS_PRODUCTION,
        path: "/",
        maxAge: refreshed.expires_in,
      });
      return res;
    }
  }

  if (claims) return NextResponse.next();

  // Unauthenticated and refresh failed.
  if (isProtectedApi(pathname)) {
    return clearAuthCookies(NextResponse.json({ error: "unauthorized" }, { status: 401 }));
  }
  const loginUrl = new URL("/login", req.url);
  loginUrl.searchParams.set("next", pathname);
  return clearAuthCookies(NextResponse.redirect(loginUrl));
}

export const config = {
  matcher: [
    "/overview/:path*",
    "/traces/:path*",
    "/alerts/:path*",
    "/models/:path*",
    "/settings/:path*",
    "/api/:path*",
  ],
};
