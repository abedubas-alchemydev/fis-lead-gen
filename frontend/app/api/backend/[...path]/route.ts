import { NextRequest, NextResponse } from "next/server";

import { getIdentityToken } from "@/lib/gcp-identity-token";

const BACKEND_BASE_URL = process.env.INTERNAL_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8001";

const STATUSES_WITHOUT_BODY = new Set<number>([204, 205, 304]);

type RouteContext = {
  params: {
    path: string[];
  };
};

async function proxyRequest(request: NextRequest, { params }: RouteContext) {
  const upstreamUrl = new URL(`/${params.path.join("/")}`, BACKEND_BASE_URL);
  request.nextUrl.searchParams.forEach((value, key) => {
    upstreamUrl.searchParams.append(key, value);
  });

  const bodyText = request.method === "GET" || request.method === "HEAD" ? undefined : await request.text();
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
    body: bodyText && bodyText.length > 0 ? bodyText : undefined,
    cache: "no-store",
    redirect: "manual"
  });

  const contentType = upstreamResponse.headers.get("content-type");

  if (STATUSES_WITHOUT_BODY.has(upstreamResponse.status)) {
    const headers = new Headers();
    if (contentType) {
      headers.set("content-type", contentType);
    }
    return new NextResponse(null, {
      status: upstreamResponse.status,
      headers
    });
  }

  const responseBody = await upstreamResponse.arrayBuffer();
  const response = new NextResponse(responseBody, {
    status: upstreamResponse.status
  });

  if (contentType) {
    response.headers.set("content-type", contentType);
  }

  return response;
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
