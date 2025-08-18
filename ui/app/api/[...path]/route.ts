import { NextRequest } from "next/server";

const TARGET = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8080"; // VM IP:PORT

async function proxy(req: NextRequest, path: string) {
  const url = `${TARGET}/${path}${req.nextUrl.search}`;
  const init: RequestInit = {
    method: req.method,
    headers: Object.fromEntries(
      [...req.headers].filter(([k]) =>
        !["host","x-forwarded-for","x-forwarded-host","x-forwarded-proto","content-length"].includes(k.toLowerCase())
      )
    ),
    body: ["GET","HEAD"].includes(req.method) ? undefined : await req.arrayBuffer(),
    redirect: "manual",
  };
  const res = await fetch(url, init);
  const headers = new Headers(res.headers);
  headers.delete("content-length");
  headers.set("cache-control", "no-store");
  return new Response(res.body, { status: res.status, headers });
}

export const runtime = "nodejs"; // needs Node runtime (not edge)
export async function GET(req: NextRequest, { params }: { params: { path: string[] } }) { 
  return proxy(req, (params.path ?? []).join("/")); 
}
export async function POST(req: NextRequest, ctx: any) { return GET(req, ctx); }
export async function PUT(req: NextRequest, ctx: any) { return GET(req, ctx); }
export async function PATCH(req: NextRequest, ctx: any) { return GET(req, ctx); }
export async function DELETE(req: NextRequest, ctx: any) { return GET(req, ctx); }