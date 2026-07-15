#!/usr/bin/env node
// Print a scrypt hash for DASHBOARD_PASSWORD_HASH in web/.env.local.
// Usage: node scripts/hash-password.mjs <password>
import { randomBytes, scryptSync } from "node:crypto";

const password = process.argv[2];
if (!password) {
  console.error("usage: node scripts/hash-password.mjs <password>");
  process.exit(1);
}

const salt = randomBytes(16);
const hash = scryptSync(password, salt, 32);
const stored = `scrypt$${salt.toString("hex")}$${hash.toString("hex")}`;
console.log(stored);
// Next.js env files run through dotenv-expand, which treats bare `$` as
// variable interpolation — the .env.local line must escape them as `\$`.
console.log(`\n# ready to paste into web/.env.local:`);
console.log(`DASHBOARD_PASSWORD_HASH=${stored.replaceAll("$", "\\$")}`);
