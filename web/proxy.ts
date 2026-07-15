import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { SESSION_COOKIE, sessionSecret, verifySessionToken } from "./app/lib/session";

const PUBLIC_PATHS = new Set(["/login", "/api/login", "/api/logout"]);

export function proxy(request: NextRequest) {
  const secret = sessionSecret();
  // No SESSION_SECRET configured → auth disabled (mirrors AUTH_ENABLED=false).
  if (!secret) return NextResponse.next();

  const { pathname } = request.nextUrl;
  if (PUBLIC_PATHS.has(pathname)) return NextResponse.next();

  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (verifySessionToken(token, secret)) return NextResponse.next();

  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const loginUrl = new URL("/login", request.url);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  // Everything except Next internals and static assets.
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
