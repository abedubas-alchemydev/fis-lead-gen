import { NextRequest, NextResponse } from "next/server";

import { getIdentityToken } from "@/lib/gcp-identity-token";

const BACKEND_BASE_URL = process.env.INTERNAL_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8001";

const STATUSES_WITHOUT_BODY = new Set<number>([204, 205, 304]);

// RFC 7230 § 6.1 hop-by-hop headers — must not be forwarded by a proxy.
const HOP_BY_HOP_HEADERS = new Set<string>([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade"
]);

// Response-side drops add content-encoding (Node's fetch auto-decompresses,
// so the bytes we hand back are already plaintext) and content-length (Next
// recomputes it from the body buffer).
const RESPONSE_DROP_HEADERS = new Set<string>([
  ...HOP_BY_HOP_HEADERS,
  "content-encoding",
  "content-length"
]);

type RouteContext = {
  params: Promise<{
    path: string[];
  }>;
};

async function proxyRequest(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  const upstreamUrl = new URL(`/${path.join("/")}`, BACKEND_BASE_URL);
  request.nextUrl.searchParams.forEach((value, key) => {
    upstreamUrl.searchParams.append(key, value);
  });

  // ArrayBuffer (not text). request.text() UTF-8-decodes with U+FFFD
  // substitution for invalid sequences, which silently corrupts any binary
  // payload — multipart file uploads in particular. Zip-based formats
  // (xlsx/docx/pptx) become unreadable because the bytes after the
  // PK\x03\x04 magic are arbitrary CRC/size data that get replaced.
  const bodyBuffer = request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer();
  const identityToken = await getIdentityToken(BACKEND_BASE_URL);

  const upstreamHeaders = new Headers(request.headers);
  upstreamHeaders.delete("host");
  upstreamHeaders.delete("content-length");
  if (identityToken) {
    upstreamHeaders.set("authorization", `Bearer ${identityToken}`);
  }

  const upstreamResponse = await fetch(upstreamUrl, {
    method: request.method,
    headers: upstreamHeaders,
    body: bodyBuffer && bodyBuffer.byteLength > 0 ? bodyBuffer : undefined,
    cache: "no-store",
    redirect: "manual"
  });

  const responseHeaders = forwardableHeaders(upstreamResponse.headers);

  if (STATUSES_WITHOUT_BODY.has(upstreamResponse.status)) {
    return new NextResponse(null, {
      status: upstreamResponse.status,
      headers: responseHeaders
    });
  }

  // SSE (and any other text/event-stream endpoint, e.g. /api/v1/alerts/stream)
  // must NOT be buffered. arrayBuffer() below pulls the whole response into
  // memory before returning — that breaks streaming entirely. Pass the
  // upstream ReadableStream through verbatim. The hop-by-hop / content-length
  // / content-encoding filters in forwardableHeaders() already strip the
  // headers Next.js will recompute, so streaming the body untouched is safe.
  const upstreamContentType = upstreamResponse.headers.get("content-type") ?? "";
  const isEventStream = upstreamContentType.toLowerCase().includes("text/event-stream");
  if (isEventStream && upstreamResponse.body) {
    return new NextResponse(upstreamResponse.body, {
      status: upstreamResponse.status,
      headers: responseHeaders
    });
  }

  const responseBody = await upstreamResponse.arrayBuffer();
  return new NextResponse(responseBody, {
    status: upstreamResponse.status,
    headers: responseHeaders
  });
}

function forwardableHeaders(upstream: Headers): Headers {
  // Forward every header that isn't hop-by-hop or recomputed by Next. Keeps
  // Location intact on 3xx (vault file download 302s to a signed GCS URL)
  // and Set-Cookie on auth-flowing endpoints.
  const out = new Headers();
  upstream.forEach((value, key) => {
    if (!RESPONSE_DROP_HEADERS.has(key.toLowerCase())) {
      out.append(key, value);
    }
  });
  return out;
}

export async function GET(request: NextRequest, context: RouteContext) {
  return proxyRequest(request, context);
}

export async function POST(request: NextRequest, context: RouteContext) {
  return proxyRequest(request, context);
}

export async function PUT(request: NextRequest, context: RouteContext) {
  return proxyRequest(request, context);
}

export async function PATCH(request: NextRequest, context: RouteContext) {
  return proxyRequest(request, context);
}

export async function DELETE(request: NextRequest, context: RouteContext) {
  return proxyRequest(request, context);
}
