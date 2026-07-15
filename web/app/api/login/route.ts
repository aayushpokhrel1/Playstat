import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { verifyPassword } from "../../lib/password";
import {
  SESSION_COOKIE,
  SESSION_MAX_AGE_SECONDS,
  createSessionToken,
  sessionSecret,
} from "../../lib/session";

export async function POST(request: NextRequest) {
  const form = await request.formData();
  const username = String(form.get("username") ?? "");
  const password = String(form.get("password") ?? "");

  const secret = sessionSecret();
  const expectedUser = process.env.DASHBOARD_USER;
  const passwordHash = process.env.DASHBOARD_PASSWORD_HASH;

  if (!secret || !expectedUser || !passwordHash) {
    // Auth not configured; nothing to log into.
    return NextResponse.redirect(new URL("/", request.url), 303);
  }

  // One generic failure path — no user enumeration.
  const userOk = username === expectedUser;
  const passOk = verifyPassword(password, passwordHash);
  if (!userOk || !passOk) {
    return NextResponse.redirect(new URL("/login?error=1", request.url), 303);
  }

  const response = NextResponse.redirect(new URL("/", request.url), 303);
  response.cookies.set({
    name: SESSION_COOKIE,
    value: createSessionToken(secret),
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_MAX_AGE_SECONDS,
  });
  return response;
}
