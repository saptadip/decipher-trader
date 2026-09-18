import { NextRequest, NextResponse } from "next/server";
import { getSession } from "@/lib/session";
import { controlPlane } from "@/lib/controlPlane";

async function ensureSession() {
  const s = await getSession();
  if (!s.operator) return NextResponse.json({ ok: false }, { status: 401 });
  return null;
}

async function forward(
  req: NextRequest,
  params: { path: string[] },
  method: "GET" | "POST" | "DELETE",
) {
  const auth = await ensureSession();
  if (auth) return auth;
  const path = "/" + params.path.join("/") + (req.nextUrl.search ?? "");
  const body = method === "GET" ? undefined : await req.text();
  const resp =
    method === "GET"
      ? await controlPlane.get(path)
      : method === "POST"
        ? await controlPlane.post(path, body ? JSON.parse(body) : undefined)
        : await controlPlane.delete(path);
  const text = await resp.text();
  return new NextResponse(text, {
    status: resp.status,
    headers: {
      "content-type": resp.headers.get("content-type") ?? "application/json",
    },
  });
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, await ctx.params, "GET");
}
export async function POST(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, await ctx.params, "POST");
}
export async function DELETE(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, await ctx.params, "DELETE");
}
