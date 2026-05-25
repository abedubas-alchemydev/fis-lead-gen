import { NextResponse } from "next/server";
import { headers } from "next/headers";

import { auth, db } from "@/lib/auth";

export async function POST(_request: Request, props: { params: Promise<{ id: string }> }) {
  const params = await props.params;
  const session = await auth.api.getSession({ headers: await headers() });

  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  if (session.user.role !== "admin") {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }
  if (session.user.id === params.id) {
    return NextResponse.json(
      { error: "Admins cannot modify their own approval status." },
      { status: 400 }
    );
  }

  const targetId = params.id;
  const client = await db.connect();
  try {
    await client.query("BEGIN");

    const existing = await client.query<{ status: string }>(
      'SELECT status FROM "user" WHERE id = $1 FOR UPDATE',
      [targetId]
    );
    if (existing.rowCount === 0) {
      await client.query("ROLLBACK");
      return NextResponse.json({ error: "User not found" }, { status: 404 });
    }
    const previousStatus = existing.rows[0].status;
    // Deactivation is the post-approval flow. If the row isn't 'active'
    // (e.g. it's already rejected, or still pending), surface that as a
    // 409 so the admin can refresh and pick the right action instead of
    // silently no-opping on a stale row.
    if (previousStatus !== "active") {
      await client.query("ROLLBACK");
      return NextResponse.json(
        { error: `User is not active (current status: ${previousStatus}).` },
        { status: 409 }
      );
    }

    await client.query(
      'UPDATE "user" SET status = $1, updated_at = NOW() WHERE id = $2',
      ["rejected", targetId]
    );

    // Kill any active sessions so a deactivated user with an outstanding
    // cookie can't continue using the app.
    await client.query('DELETE FROM session WHERE user_id = $1', [targetId]);

    await client.query(
      "INSERT INTO audit_log (user_id, action, details) VALUES ($1, $2, $3)",
      [
        session.user.id,
        "user_deactivated",
        JSON.stringify({
          target_user_id: targetId,
          previous_status: previousStatus,
        }),
      ]
    );

    await client.query("COMMIT");
  } catch (err) {
    await client.query("ROLLBACK");
    console.error("[ADMIN_DEACTIVATE] Failed:", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Deactivation failed" },
      { status: 500 }
    );
  } finally {
    client.release();
  }

  return NextResponse.json({ ok: true });
}
