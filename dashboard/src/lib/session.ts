import { getIronSession, IronSession } from "iron-session";
import bcrypt from "bcryptjs";
import { cookies } from "next/headers";

export interface SessionData {
  operator?: string;
}

const cookieName = "decipher_session";

export async function getSession(): Promise<IronSession<SessionData>> {
  const password = process.env.DASHBOARD_SESSION_SECRET;
  if (!password || password.length < 32) {
    throw new Error("DASHBOARD_SESSION_SECRET must be at least 32 chars");
  }
  // Next.js 15: cookies() returns a Promise; await is required (was sync in Next 14).
  return getIronSession<SessionData>(await cookies(), { password, cookieName });
}

export async function hashPassword(plain: string): Promise<string> {
  return bcrypt.hash(plain, 10);
}

export async function verifyPassword(plain: string, hash: string): Promise<boolean> {
  return bcrypt.compare(plain, hash);
}
