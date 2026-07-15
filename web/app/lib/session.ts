import { createHmac, timingSafeEqual } from "node:crypto";

export const SESSION_COOKIE = "playstat_session";
export const SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60; // 7 days

/** Auth is enabled only when a session secret is configured — mirrors the
 * API's AUTH_ENABLED flag so dev without env still works. */
export function sessionSecret(): string | undefined {
  const secret = process.env.SESSION_SECRET;
  return secret && secret.length > 0 ? secret : undefined;
}

function sign(expiry: string, secret: string): string {
  return createHmac("sha256", secret).update(expiry).digest("hex");
}

/** Token format: `<expiry_unix>.<hmac_sha256(expiry, SESSION_SECRET)>`. */
export function createSessionToken(secret: string): string {
  const expiry = String(Math.floor(Date.now() / 1000) + SESSION_MAX_AGE_SECONDS);
  return `${expiry}.${sign(expiry, secret)}`;
}

export function verifySessionToken(token: string | undefined, secret: string): boolean {
  if (!token) return false;
  const dot = token.indexOf(".");
  if (dot === -1) return false;
  const expiry = token.slice(0, dot);
  const mac = token.slice(dot + 1);
  if (!/^\d+$/.test(expiry) || !/^[0-9a-f]{64}$/.test(mac)) return false;
  if (Number(expiry) <= Math.floor(Date.now() / 1000)) return false;
  const expected = Buffer.from(sign(expiry, secret), "hex");
  const presented = Buffer.from(mac, "hex");
  return expected.length === presented.length && timingSafeEqual(expected, presented);
}
