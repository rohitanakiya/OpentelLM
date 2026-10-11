// JWT verification and cookie helpers.
//
// All access/refresh tokens are stored in HTTP-only, SameSite=Strict
// cookies. Browser JS NEVER touches the token. Set/clear is centralised
// in this module so cookie attributes stay consistent.

import { jwtVerify } from "jose";
import { cookies } from "next/headers";
import { NextResponse, type NextRequest } from "next/server";
import { env, jwtSecret } from "./env";

export const ACCESS_COOKIE  = "otlm_access";
export const REFRESH_COOKIE = "otlm_refresh";

export type AccessClaims = {
  sub: string;
  tenant_id: string;
  email: string;
  role: string;
  type: "access";
  iat: number;
  exp: number;
};

/** Verify an access JWT signature + expiry. Returns null on failure. */
export async function verifyAccess(token: string): Promise<AccessClaims | null> {
  const secret = jwtSecret(); // throws on a missing/weak secret: a config error, not a bad token
  try {
    const { payload } = await jwtVerify(token, secret, {
      algorithms: [env.JWT_ALGORITHM],
    });
    if (payload.type !== "access") return null;
    return payload as unknown as AccessClaims;
  } catch {
    return null;
  }
}

/** Read access claims from cookie in a Server Component / Route Handler. */
export async function getCurrentTenant(): Promise<AccessClaims | null> {
  const token = cookies().get(ACCESS_COOKIE)?.value;
  if (!token) return null;
  return verifyAccess(token);
}

/** Same as above but reads from a NextRequest (for middleware). */
export async function getCurrentTenantFromRequest(
  req: NextRequest,
): Promise<AccessClaims | null> {
  const token = req.cookies.get(ACCESS_COOKIE)?.value;
  if (!token) return null;
  return verifyAccess(token);
}

const COOKIE_BASE = {
  httpOnly: true,
  sameSite: "strict" as const,
  secure: env.COOKIE_SECURE,
  path: "/",
};

/** Attach access + refresh cookies to a NextResponse. */
export function setAuthCookies(
  res: NextResponse,
  accessToken: string,
  refreshToken: string,
  accessMaxAgeS: number,
): NextResponse {
  res.cookies.set(ACCESS_COOKIE, accessToken, {
    ...COOKIE_BASE,
    maxAge: accessMaxAgeS,
  });
  res.cookies.set(REFRESH_COOKIE, refreshToken, {
    ...COOKIE_BASE,
    maxAge: 60 * 60 * 24 * 7, // 7 days, matches auth-service default
  });
  return res;
}

export function clearAuthCookies(res: NextResponse): NextResponse {
  res.cookies.delete(ACCESS_COOKIE);
  res.cookies.delete(REFRESH_COOKIE);
  return res;
}

/** Standard 401 response for API routes. */
export function unauthorized(): NextResponse {
  return NextResponse.json({ error: "unauthorized" }, { status: 401 });
}
