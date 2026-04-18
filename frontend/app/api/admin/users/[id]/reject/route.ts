import { NextResponse } from "next/server";
import { headers } from "next/headers";

import { auth, db } from "@/lib/auth";

export async function POST(
  _request: Request,
  { params }: { params: { id: string } }
) {
  const session = await auth.api.getSession({ headers: headers() });

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

    await client.query(
      'UPDATE "user" SET status = $1, updated_at = NOW() WHERE id = $2',
      ["rejected", targetId]
    );

    // Kill any active sessions so a rejected user with an outstanding cookie
    // can't continue using the app.
    await client.query('DELETE FROM session WHERE user_id = $1', [targetId]);

    await client.query(
      "INSERT INTO audit_log (user_id, action, details) VALUES ($1, $2, $3)",
      [
        session.user.id,
        "user_rejected",
        JSON.stringify({
          target_user_id: targetId,
          previous_status: previousStatus,
        }),
      ]
    );

    await client.query("COMMIT");
  } catch (err) {
    await client.query("ROLLBACK");
    console.error("[ADMIN_REJECT] Failed:", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Rejection failed" },
      { status: 500 }
    );
  } finally {
    client.release();
  }

  return NextResponse.json({ ok: true });
}
