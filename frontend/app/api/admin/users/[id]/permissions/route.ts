import { headers } from "next/headers";
import { NextResponse } from "next/server";

import { auth, db } from "@/lib/auth";
import { ALL_FEATURE_KEYS } from "@/lib/feature-permissions";

const ALLOWED_KEYS = new Set<string>(ALL_FEATURE_KEYS);

export async function PUT(request: Request, props: { params: Promise<{ id: string }> }) {
  const params = await props.params;
  const session = await auth.api.getSession({ headers: await headers() });

  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  if (session.user.role !== "admin") {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body." }, { status: 400 });
  }
  const raw = (body as { feature_permissions?: unknown })?.feature_permissions;
  if (!Array.isArray(raw) || raw.some((k) => typeof k !== "string")) {
    return NextResponse.json(
      { error: "feature_permissions must be an array of strings." },
      { status: 400 }
    );
  }
  const unique = Array.from(new Set(raw as string[]));
  const invalid = unique.filter((k) => !ALLOWED_KEYS.has(k));
  if (invalid.length > 0) {
    return NextResponse.json(
      { error: `Unknown feature key(s): ${invalid.join(", ")}` },
      { status: 400 }
    );
  }
  unique.sort();

  const targetId = params.id;
  const client = await db.connect();
  try {
    await client.query("BEGIN");

    const existing = await client.query<{
      role: string;
      feature_permissions: string[] | null;
    }>(
      'SELECT role, feature_permissions FROM "user" WHERE id = $1 FOR UPDATE',
      [targetId]
    );
    if (existing.rowCount === 0) {
      await client.query("ROLLBACK");
      return NextResponse.json({ error: "User not found" }, { status: 404 });
    }
    const previous = Array.isArray(existing.rows[0].feature_permissions)
      ? [...existing.rows[0].feature_permissions].sort()
      : [];

    const sameAsBefore =
      previous.length === unique.length && previous.every((v, i) => v === unique[i]);

    if (!sameAsBefore) {
      await client.query(
        'UPDATE "user" SET feature_permissions = $1::jsonb, updated_at = NOW() WHERE id = $2',
        [JSON.stringify(unique), targetId]
      );
      await client.query(
        "INSERT INTO audit_log (user_id, action, details) VALUES ($1, $2, $3)",
        [
          session.user.id,
          "user_feature_permissions_changed",
          JSON.stringify({
            target_user_id: targetId,
            previous,
            next: unique,
          }),
        ]
      );
    }

    await client.query("COMMIT");
    return NextResponse.json({ ok: true, feature_permissions: unique, changed: !sameAsBefore });
  } catch (err) {
    await client.query("ROLLBACK");
    console.error("[ADMIN_PERMISSIONS] Failed:", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Update failed" },
      { status: 500 }
    );
  } finally {
    client.release();
  }
}
