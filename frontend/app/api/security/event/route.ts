import { headers } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

import { auth, db } from "@/lib/auth";

const ALLOWED_KINDS = new Set([
  "copy_blocked",
  "right_click",
  "print_blocked",
  "devtools_open",
  "shortcut_blocked",
  "clipboard_cleared",
]);

// Per-session rate limit: 60 events/minute. Anything beyond is dropped
// silently — the client already samples 1-in-10, so 60/min == sustained
// rapid-fire that we don't want polluting audit_log either way.
const RATE_LIMIT_WINDOW_MS = 60_000;
const RATE_LIMIT_MAX = 60;
const sessionWindows = new Map<string, { resetAt: number; count: number }>();

function checkRateLimit(sessionId: string): boolean {
  const now = Date.now();
  const window = sessionWindows.get(sessionId);
  if (!window || window.resetAt < now) {
    sessionWindows.set(sessionId, { resetAt: now + RATE_LIMIT_WINDOW_MS, count: 1 });
    return true;
  }
  if (window.count >= RATE_LIMIT_MAX) return false;
  window.count += 1;
  return true;
}

interface SecurityEventBody {
  kind: unknown;
  meta?: unknown;
}

export async function POST(request: NextRequest) {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user) {
    // No session → silently drop; never reveal auth state to the client
    // logger. Status 204 keeps sendBeacon happy without leaking.
    return new NextResponse(null, { status: 204 });
  }

  if (!checkRateLimit(session.session.id)) {
    return new NextResponse(null, { status: 204 });
  }

  let payload: SecurityEventBody;
  try {
    payload = (await request.json()) as SecurityEventBody;
  } catch {
    return new NextResponse(null, { status: 204 });
  }

  if (typeof payload.kind !== "string" || !ALLOWED_KINDS.has(payload.kind)) {
    return new NextResponse(null, { status: 204 });
  }

  const meta = payload.meta && typeof payload.meta === "object" ? payload.meta : null;
  const ip =
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ??
    request.headers.get("x-real-ip") ??
    null;
  const userAgent = request.headers.get("user-agent") ?? null;

  try {
    await db.query(
      `INSERT INTO audit_log (user_id, action, details, timestamp)
       VALUES ($1, $2, $3, NOW())`,
      [
        session.user.id,
        `security.${payload.kind}`,
        JSON.stringify({
          ...(meta ?? {}),
          ip,
          user_agent: userAgent,
          session_id: session.session.id,
        }),
      ]
    );
  } catch (err) {
    console.error("[SECURITY] Failed to record event:", err);
    // Don't surface the failure to the client — the logging path must
    // never visibly break the UI it's protecting.
  }

  return new NextResponse(null, { status: 204 });
}
