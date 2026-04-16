import { NextRequest, NextResponse } from "next/server";

const BACKEND_BASE_URL = process.env.INTERNAL_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8001";

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
  const upstreamResponse = await fetch(upstreamUrl, {
    method: request.method,
    headers: {
      accept: request.headers.get("accept") ?? "application/json",
      "content-type": request.headers.get("content-type") ?? "application/json",
      cookie: request.headers.get("cookie") ?? "",
      origin: BACKEND_BASE_URL
    },
    body: bodyText && bodyText.length > 0 ? bodyText : undefined,
    cache: "no-store"
  });

  const responseBody = await upstreamResponse.arrayBuffer();
  const response = new NextResponse(responseBody, {
    status: upstreamResponse.status
  });

  const contentType = upstreamResponse.headers.get("content-type");
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
