import { getSession } from "@/lib/session";
import { redirect } from "next/navigation";

export default async function Home() {
  const session = await getSession();
  if (!session.operator) redirect("/login");
  return <main style={{ padding: 24 }}><h1>decipher-trader</h1><p>Strategies list — Task 20.</p></main>;
}
