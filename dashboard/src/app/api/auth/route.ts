import { NextRequest, NextResponse } from "next/server";
import { getSession, verifyPassword } from "@/lib/session";

export async function POST(req: NextRequest) {
  const { username, password } = await req.json();
  const expectedUser = process.env.DASHBOARD_OPERATOR_USERNAME;
  const hash = process.env.DASHBOARD_OPERATOR_PASSWORD_BCRYPT;
  if (!expectedUser || !hash) return NextResponse.json({ ok: false }, { status: 500 });
  if (username !== expectedUser) return NextResponse.json({ ok: false }, { status: 401 });
  if (!(await verifyPassword(password, hash))) return NextResponse.json({ ok: false }, { status: 401 });

  const session = await getSession();
  session.operator = expectedUser;
  await session.save();
  return NextResponse.json({ ok: true });
}

export async function DELETE() {
  const session = await getSession();
  session.destroy();
  return NextResponse.json({ ok: true });
}
