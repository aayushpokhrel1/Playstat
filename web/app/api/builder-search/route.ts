import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { searchBuilder } from "../../lib/api";

function numberParam(searchParams: URLSearchParams, key: string): number | undefined {
  const raw = searchParams.get(key);
  if (raw == null || raw === "") return undefined;
  const n = Number(raw);
  return Number.isFinite(n) ? n : undefined;
}

// Thin server-side proxy: the actual search stays in Python. This route
// exists only so the API key never has to reach the browser.
export async function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl;

  const target_payout = numberParam(searchParams, "target_payout");
  const min_prob = numberParam(searchParams, "min_prob");
  const max_legs = numberParam(searchParams, "max_legs");
  const sportRaw = searchParams.get("sport");
  const sport = sportRaw === "nfl" || sportRaw === "nba" ? sportRaw : "mlb";

  if (target_payout == null && min_prob == null) {
    return NextResponse.json(
      { error: "target_payout or min_prob is required" },
      { status: 422 }
    );
  }

  try {
    const result = await searchBuilder({ target_payout, min_prob, max_legs, sport });
    return NextResponse.json(result);
  } catch {
    return NextResponse.json({ error: "search failed" }, { status: 502 });
  }
}
