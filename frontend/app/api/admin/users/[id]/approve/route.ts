import { NextResponse } from "next/server";
import { headers } from "next/headers";

import { auth, db } from "@/lib/auth";
import { sendApprovalNotificationEmail } from "@/lib/email";

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
  let approvedUser: { email: string; name: string } | null = null;

  const client = await db.connect();
  try {
    await client.query("BEGIN");

    const existing = await client.query<{
      status: string;
      email: string;
      name: string;
    }>(
      'SELECT status, email, name FROM "user" WHERE id = $1 FOR UPDATE',
      [targetId]
    );
    if (existing.rowCount === 0) {
      await client.query("ROLLBACK");
      return NextResponse.json({ error: "User not found" }, { status: 404 });
    }
    const previousStatus = existing.rows[0].status;
    approvedUser = {
      email: existing.rows[0].email,
      name: existing.rows[0].name,
    };

    await client.query(
      'UPDATE "user" SET status = $1, updated_at = NOW() WHERE id = $2',
      ["active", targetId]
    );

    await client.query(
      "INSERT INTO audit_log (user_id, action, details) VALUES ($1, $2, $3)",
      [
        session.user.id,
        "user_approved",
        JSON.stringify({
          target_user_id: targetId,
          previous_status: previousStatus,
        }),
      ]
    );

    await client.query("COMMIT");
  } catch (err) {
    await client.query("ROLLBACK");
    console.error("[ADMIN_APPROVE] Failed:", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Approval failed" },
      { status: 500 }
    );
  } finally {
    client.release();
  }

  // Fire-and-forget approval notification to the user. A send failure must
  // not roll back the approval itself — the status change is already committed.
  if (approvedUser) {
    const appUrl =
      process.env.BETTER_AUTH_URL ??
      process.env.NEXT_PUBLIC_APP_URL ??
      "http://localhost:3000";
    sendApprovalNotificationEmail({
      user: approvedUser,
      loginUrl: `${appUrl}/login`,
    }).catch((err) => {
      console.error(
        "[ADMIN_APPROVE] Failed to send approval notification email:",
        err
      );
    });
  }

  return NextResponse.json({ ok: true });
}
