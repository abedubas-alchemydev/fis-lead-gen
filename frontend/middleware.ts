import { NextRequest, NextResponse } from "next/server";
import { getSessionCookie } from "better-auth/cookies";

import { DATA_EXPORT_ENABLED, EMAIL_EXTRACTION_ENABLED } from "@/lib/feature-flags";

const protectedPrefixes = ["/dashboard", "/master-list", "/alerts", "/settings"];

// Hidden surfaces — no data export / extraction in-app. Backend code is
// intact; these routes just bounce to the dashboard. Flip the flags in
// lib/feature-flags.ts to restore.
const hiddenPrefixes = [
  ...(EMAIL_EXTRACTION_ENABLED ? [] : ["/email-extractor"]),
  ...(DATA_EXPORT_ENABLED ? [] : ["/export"]),
];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (hiddenPrefixes.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`))) {
    return NextResponse.redirect(new URL("/dashboard", request.url));
  }

  const requiresAuth = protectedPrefixes.some((prefix) =>
    pathname === prefix || pathname.startsWith(`${prefix}/`),
  );

  if (!requiresAuth) {
    return NextResponse.next();
  }

  const sessionCookie = getSessionCookie(request);
  if (!sessionCookie) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/dashboard/:path*",
    "/master-list/:path*",
    "/alerts/:path*",
    "/settings/:path*",
    "/export/:path*",
    "/email-extractor/:path*"
  ]
};
