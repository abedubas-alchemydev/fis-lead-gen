import nodemailer, { type Transporter } from "nodemailer";

let cachedTransporter: Transporter | null = null;

function getTransporter(): Transporter {
  if (cachedTransporter) return cachedTransporter;

  const host = process.env.SMTP_HOST;
  const port = Number(process.env.SMTP_PORT ?? 587);
  const user = process.env.SMTP_USER;
  const pass = process.env.SMTP_PASSWORD;

  if (!host || !user || !pass) {
    throw new Error(
      "SMTP transport not configured. Missing SMTP_HOST / SMTP_USER / SMTP_PASSWORD."
    );
  }

  cachedTransporter = nodemailer.createTransport({
    host,
    port,
    secure: false,
    requireTLS: true,
    auth: { user, pass },
  });
  return cachedTransporter;
}

const fromAddress = process.env.EMAIL_FROM ?? "noreply@alchemydev.io";
const appName = "DOX — Institutional Finance Intelligence";

// ─── Shared enterprise email wrapper ────────────────────────────────
function buildHtml({
  preheader,
  heading,
  body,
  ctaUrl,
  ctaLabel,
  footer,
}: {
  preheader: string;
  heading: string;
  body: string;
  ctaUrl?: string;
  ctaLabel?: string;
  footer: string;
}) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="color-scheme" content="light" />
  <title>${heading}</title>
  <style>
    body { margin:0; padding:0; background:#f4f7fb; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,Arial,sans-serif; }
    .preheader { display:none; max-height:0; overflow:hidden; mso-hide:all; }
    .container { max-width:560px; margin:40px auto; background:#ffffff; border-radius:16px; box-shadow:0 4px 24px rgba(10,31,63,0.08); overflow:hidden; }
    .header { background:#0A1F3F; padding:32px 40px; }
    .header h1 { color:#ffffff; font-size:14px; font-weight:600; letter-spacing:0.2em; text-transform:uppercase; margin:0; }
    .body { padding:40px; }
    .body h2 { color:#0A1F3F; font-size:22px; font-weight:600; margin:0 0 16px; }
    .body p { color:#475569; font-size:15px; line-height:1.7; margin:0 0 16px; }
    .cta { display:inline-block; background:#1B5E9E; color:#ffffff!important; text-decoration:none; padding:14px 32px; border-radius:12px; font-size:15px; font-weight:600; margin:8px 0 24px; }
    .divider { border:none; border-top:1px solid #e2e8f0; margin:24px 0; }
    .footer { padding:24px 40px 32px; }
    .footer p { color:#94a3b8; font-size:12px; line-height:1.6; margin:0; }
    .footer a { color:#1B5E9E; text-decoration:none; }
  </style>
</head>
<body>
  <span class="preheader">${preheader}</span>
  <div class="container">
    <div class="header">
      <h1>${appName}</h1>
    </div>
    <div class="body">
      <h2>${heading}</h2>
      ${body}
      ${ctaUrl && ctaLabel ? `<a href="${ctaUrl}" class="cta">${ctaLabel}</a>` : ""}
      <hr class="divider" />
      <p style="font-size:13px;color:#94a3b8;">${footer}</p>
    </div>
    <div class="footer">
      <p>&copy; ${new Date().getFullYear()} ${appName}. All rights reserved.</p>
      <p>This is an automated message. Please do not reply directly to this email.</p>
    </div>
  </div>
</body>
</html>`;
}

// ─── Password Reset Email ───────────────────────────────────────────
export async function sendPasswordResetEmail({
  user,
  url,
}: {
  user: { email: string; name: string };
  url: string;
}) {
  const html = buildHtml({
    preheader: "Reset your password for DOX",
    heading: "Reset your password",
    body: `
      <p>Hi ${user.name || "there"},</p>
      <p>We received a request to reset the password for the account associated with <strong>${user.email}</strong>.</p>
      <p>Click the button below to choose a new password. This link expires in 1 hour.</p>
    `,
    ctaUrl: url,
    ctaLabel: "Reset Password",
    footer:
      "If you did not request a password reset, you can safely ignore this email. Your password will not be changed.",
  });

  try {
    const info = await getTransporter().sendMail({
      from: fromAddress,
      to: user.email,
      subject: "Reset your password",
      html,
    });
    console.log(
      `[EMAIL][password-reset] sent ok messageId=${info.messageId} to=${user.email}`
    );
  } catch (error: unknown) {
    console.error(`[EMAIL][password-reset] FAIL to=${user.email}:`, error);
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`Email delivery failed: ${message}`);
  }
}

// ─── New sign-in alert (single-active-session) ──────────────────────
// Sent when a fresh login ends the account's sessions on other devices.
// Best-effort: never throws, so the login hook can fire-and-forget it.
export async function sendNewSignInSignoutAlert({
  user,
  ip,
  userAgent,
  settingsUrl,
}: {
  user: { email: string; name: string };
  ip: string | null;
  userAgent: string | null;
  settingsUrl?: string;
}) {
  const html = buildHtml({
    preheader: "A new sign-in ended your other DOX sessions",
    heading: "New sign-in detected",
    body: `
      <p>Hi ${user.name || "there"},</p>
      <p>Your DOX account (<strong>${user.email}</strong>) was just signed in on a new device, so we signed you out on every other device. Only the newest device stays signed in.</p>
      <p>
        <strong>New device IP:</strong> ${ip || "Unknown"}<br />
        <strong>Browser:</strong> ${userAgent || "Unknown"}
      </p>
      <p>If this was you, no action is needed. If it wasn't, change your password right away.</p>
    `,
    ctaUrl: settingsUrl,
    ctaLabel: settingsUrl ? "Review account" : undefined,
    footer:
      "You're receiving this because DOX keeps your account signed in on one device at a time for security.",
  });

  try {
    const info = await getTransporter().sendMail({
      from: fromAddress,
      to: user.email,
      subject: "New sign-in on your DOX account",
      html,
    });
    console.log(
      `[EMAIL][new-signin] sent ok messageId=${info.messageId} to=${user.email}`
    );
  } catch (error: unknown) {
    // Best-effort alert — swallow so a mail outage never blocks a login.
    console.error(`[EMAIL][new-signin] FAIL to=${user.email}:`, error);
  }
}

// ─── Email Verification Email ───────────────────────────────────────
export async function sendVerificationEmail({
  user,
  url,
}: {
  user: { email: string; name: string };
  url: string;
}) {
  const html = buildHtml({
    preheader: "Verify your email — your account will then be reviewed by our team",
    heading: "Verify your email address",
    body: `
      <p>Hi ${user.name || "there"},</p>
      <p>Thanks for creating an account. Please verify your email address so we know this inbox belongs to you.</p>
      <p><strong>Next step after verification:</strong> our team reviews every new account before access is granted. You'll get a separate email as soon as you're approved. The verification link expires in 24 hours.</p>
    `,
    ctaUrl: url,
    ctaLabel: "Verify Email Address",
    footer:
      "If you did not create an account, you can safely ignore this email.",
  });

  try {
    const info = await getTransporter().sendMail({
      from: fromAddress,
      to: user.email,
      subject: "Verify your email address",
      html,
    });
    console.log(
      `[EMAIL][verify] sent ok messageId=${info.messageId} to=${user.email}`
    );
  } catch (error: unknown) {
    console.error(`[EMAIL][verify] FAIL to=${user.email}:`, error);
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`Email delivery failed: ${message}`);
  }
}

// ─── Admin Approval Request Email ───────────────────────────────────
export async function sendAdminApprovalRequestEmail({
  newUser,
  adminEmails,
  settingsUrl,
}: {
  newUser: { email: string; name: string; createdAt: Date | string };
  adminEmails: string[];
  settingsUrl: string;
}) {
  const signupTime =
    typeof newUser.createdAt === "string"
      ? newUser.createdAt
      : newUser.createdAt.toISOString();

  const html = buildHtml({
    preheader: `New signup pending approval: ${newUser.email}`,
    heading: "New signup pending approval",
    body: `
      <p>A new account is awaiting your review before it can sign in.</p>
      <p>
        <strong>Name:</strong> ${newUser.name || "(not provided)"}<br />
        <strong>Email:</strong> ${newUser.email}<br />
        <strong>Signed up:</strong> ${signupTime}
      </p>
      <p>Open the admin panel to approve or reject this account.</p>
    `,
    ctaUrl: settingsUrl,
    ctaLabel: "Open admin panel",
    footer:
      "You are receiving this because you hold the admin role in DOX.",
  });

  try {
    const info = await getTransporter().sendMail({
      from: fromAddress,
      to: adminEmails.join(", "),
      subject: "New DOX signup pending approval",
      html,
    });
    console.log(
      `[EMAIL][admin-approval] sent ok messageId=${info.messageId} to=${adminEmails.length}admin(s)`
    );
  } catch (error: unknown) {
    console.error(
      `[EMAIL][admin-approval] FAIL to=${adminEmails.length}admin(s):`,
      error
    );
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`Email delivery failed: ${message}`);
  }
}

// ─── Approval Notification (to the approved user) ───────────────────
export async function sendApprovalNotificationEmail({
  user,
  loginUrl,
}: {
  user: { email: string; name: string };
  loginUrl: string;
}) {
  const html = buildHtml({
    preheader: "Your account has been approved — you can now sign in.",
    heading: "You're approved",
    body: `
      <p>Hi ${user.name || "there"},</p>
      <p>Good news — an admin has approved your account. You can now sign in to DOX.</p>
      <p>If you haven't already verified your email, please click the verification link from the earlier email we sent you.</p>
    `,
    ctaUrl: loginUrl,
    ctaLabel: "Sign in",
    footer:
      "If you did not sign up, or if you believe this approval was granted in error, please contact support.",
  });

  try {
    const info = await getTransporter().sendMail({
      from: fromAddress,
      to: user.email,
      subject: "Your DOX account has been approved",
      html,
    });
    console.log(
      `[EMAIL][user-approved] sent ok messageId=${info.messageId} to=${user.email}`
    );
  } catch (error: unknown) {
    console.error(`[EMAIL][user-approved] FAIL to=${user.email}:`, error);
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`Email delivery failed: ${message}`);
  }
}

// ─── Reactivation Notification (to the reactivated user) ────────────
export async function sendReactivationNotificationEmail({
  user,
  loginUrl,
}: {
  user: { email: string; name: string };
  loginUrl: string;
}) {
  const html = buildHtml({
    preheader: "Your account has been reactivated — you can sign in again.",
    heading: "You're back in",
    body: `
      <p>Hi ${user.name || "there"},</p>
      <p>An admin has reactivated your DOX account. Workspace access has been restored and you can sign in again.</p>
      <p>Your saved lists, favorites, and feature permissions are preserved.</p>
    `,
    ctaUrl: loginUrl,
    ctaLabel: "Sign in",
    footer:
      "If you did not expect this, or if you believe your account should remain deactivated, please contact support.",
  });

  try {
    const info = await getTransporter().sendMail({
      from: fromAddress,
      to: user.email,
      subject: "Your DOX account has been reactivated",
      html,
    });
    console.log(
      `[EMAIL][user-reactivated] sent ok messageId=${info.messageId} to=${user.email}`
    );
  } catch (error: unknown) {
    console.error(`[EMAIL][user-reactivated] FAIL to=${user.email}:`, error);
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`Email delivery failed: ${message}`);
  }
}
