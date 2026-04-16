import { Resend } from "resend";

const resend = new Resend(process.env.RESEND_API_KEY);
const fromAddress = process.env.EMAIL_FROM ?? "Client Clearing Lead Gen Engine <onboarding@resend.dev>";
const appName = "Client Clearing Lead Gen Engine";

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
    preheader: "Reset your password for the Lead Gen Engine",
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

  const { error } = await resend.emails.send({
    from: fromAddress,
    to: user.email,
    subject: "Reset your password",
    html,
  });

  if (error) {
    console.error("[EMAIL] Failed to send password reset:", error);
    throw new Error(`Email delivery failed: ${error.message}`);
  }

  console.log(`[EMAIL] Password reset email sent to ${user.email}`);
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
    preheader: "Verify your email to activate your Lead Gen Engine account",
    heading: "Verify your email address",
    body: `
      <p>Hi ${user.name || "there"},</p>
      <p>Thank you for creating an account. Please verify your email address to activate your account and access the broker-dealer intelligence platform.</p>
      <p>Click the button below to verify. This link expires in 24 hours.</p>
    `,
    ctaUrl: url,
    ctaLabel: "Verify Email Address",
    footer:
      "If you did not create an account, you can safely ignore this email.",
  });

  const { error } = await resend.emails.send({
    from: fromAddress,
    to: user.email,
    subject: "Verify your email address",
    html,
  });

  if (error) {
    console.error("[EMAIL] Failed to send verification email:", error);
    throw new Error(`Email delivery failed: ${error.message}`);
  }

  console.log(`[EMAIL] Verification email sent to ${user.email}`);
}
