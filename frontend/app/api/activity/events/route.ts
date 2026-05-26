import { headers } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

import { auth, db } from "@/lib/auth";

// Per-session rate limit: 600 events/minute. Two orders of magnitude
// above the security route's 60/min cap because activity tracking fans
// out per click + nav + form submit, while security events fire only on
// shield-blocked actions. 10 events/sec sustained is the ceiling —
// anything above is almost certainly a client bug or hostile.
//
// In-process bucket: fine while the FE runs as a single Next.js
// instance. Revisit when we go horizontally multi-instance.
const RATE_LIMIT_WINDOW_MS = 60_000;
const RATE_LIMIT_MAX = 600;
const sessionWindows = new Map<string, { resetAt: number; count: number }>();

function checkRateLimit(sessionId: string, increment: number): boolean {
  const now = Date.now();
  const window = sessionWindows.get(sessionId);
  if (!window || window.resetAt < now) {
    sessionWindows.set(sessionId, { resetAt: now + RATE_LIMIT_WINDOW_MS, count: increment });
    return true;
  }
  if (window.count + increment > RATE_LIMIT_MAX) return false;
  window.count += increment;
  return true;
}

const ALLOWED_ACTIONS = new Set([
  "nav_view",
  "nav_click",
  "link_open",
  "search_query",
  "input_used",
]);

// Whitelist scrubber: only these keys survive into the persisted
// ``details`` JSONB. Everything else is dropped silently — the FE
// shouldn't send PII keys, but defense in depth.
const ALLOWED_META_KEYS = new Set([
  "nav_label",
  "from_path",
  "to_path",
  "host",
  "target",
  "filter_keys",
  "query_present",
  "query_length",
  "field_name",
  "field_id",
  "field_length",
  "field_populated",
  "form_id",
  "button_label",
]);

const EMAIL_RE = /[\w.+-]+@[\w-]+\.[\w.-]+/;
const PHONE_RE = /\d{7,}/;
const MAX_STRING_LEN = 80;
const MAX_BATCH = 100;

interface RawEvent {
  action: unknown;
  path: unknown;
  meta: unknown;
}

interface ScrubbedEvent {
  action: string;
  path: string | null;
  details: Record<string, unknown> | null;
}

// Collapse numeric path segments to ``:id`` so high-cardinality URLs
// don't blow up index size (``/master-list/12345`` → ``/master-list/:id``).
function normalizePath(raw: string): string {
  const stripped = raw.split("?")[0]?.split("#")[0] ?? "";
  return stripped
    .split("/")
    .map((seg) => (/^\d+$/.test(seg) ? ":id" : seg))
    .join("/")
    .slice(0, 512);
}

function scrubString(value: string): string | null {
  if (value.length > MAX_STRING_LEN) return null;
  if (EMAIL_RE.test(value) || PHONE_RE.test(value)) return null;
  return value;
}

function scrubMeta(raw: unknown): Record<string, unknown> | null {
  if (!raw || typeof raw !== "object") return null;
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (!ALLOWED_META_KEYS.has(key)) continue;
    if (typeof value === "boolean") {
      out[key] = value;
    } else if (typeof value === "number") {
      if (Number.isFinite(value) && value >= 0 && value <= 5000) {
        out[key] = Math.floor(value);
      }
    } else if (typeof value === "string") {
      const safe = scrubString(value);
      if (safe !== null) out[key] = safe;
    } else if (key === "filter_keys" && Array.isArray(value)) {
      const safe = value
        .filter((v): v is string => typeof v === "string")
        .map((v) => scrubString(v))
        .filter((v): v is string => v !== null)
        .slice(0, 12);
      if (safe.length > 0) out[key] = safe;
    }
  }
  return Object.keys(out).length > 0 ? out : null;
}

function scrubEvent(raw: RawEvent): ScrubbedEvent | null {
  if (typeof raw.action !== "string" || !ALLOWED_ACTIONS.has(raw.action)) return null;
  const path = typeof raw.path === "string" ? normalizePath(raw.path) : null;
  const details = scrubMeta(raw.meta);
  return { action: raw.action, path, details };
}

export async function POST(request: NextRequest) {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user) return new NextResponse(null, { status: 204 });

  let payload: { events?: unknown };
  try {
    payload = (await request.json()) as { events?: unknown };
  } catch {
    return new NextResponse(null, { status: 204 });
  }

  if (!Array.isArray(payload.events)) return new NextResponse(null, { status: 204 });
  if (payload.events.length === 0) return new NextResponse(null, { status: 204 });
  if (payload.events.length > MAX_BATCH) return new NextResponse(null, { status: 204 });

  const scrubbed: ScrubbedEvent[] = [];
  for (const raw of payload.events) {
    if (!raw || typeof raw !== "object") continue;
    const ev = scrubEvent(raw as RawEvent);
    if (ev) scrubbed.push(ev);
  }
  if (scrubbed.length === 0) return new NextResponse(null, { status: 204 });

  if (!checkRateLimit(session.session.id, scrubbed.length)) {
    return new NextResponse(null, { status: 204 });
  }

  // Multi-row INSERT in one round-trip. Each event takes 5 placeholders
  // (user_id, session_id, action, path, details); created_at is the
  // table default NOW(). pg.Pool.query doesn't auto-stringify JSONB
  // params, so details is JSON.stringify'd and cast via ``::jsonb``.
  const userId = session.user.id;
  const sessionId = session.session.id;
  const values: unknown[] = [];
  const rows: string[] = [];
  for (let i = 0; i < scrubbed.length; i += 1) {
    const ev = scrubbed[i]!;
    const base = i * 5;
    rows.push(`($${base + 1}, $${base + 2}, $${base + 3}, $${base + 4}, $${base + 5}::jsonb)`);
    values.push(
      userId,
      sessionId,
      ev.action,
      ev.path,
      ev.details ? JSON.stringify(ev.details) : null,
    );
  }

  try {
    await db.query(
      `INSERT INTO user_activity (user_id, session_id, action, path, details)
       VALUES ${rows.join(", ")}`,
      values,
    );
  } catch (err) {
    console.error("[ACTIVITY] Failed to record events:", err);
  }

  return new NextResponse(null, { status: 204 });
}
