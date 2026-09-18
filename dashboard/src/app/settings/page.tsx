export const dynamic = "force-dynamic";

import { getSession } from "@/lib/session";
import { redirect } from "next/navigation";
import KillSwitch from "@/components/KillSwitch";

export default async function Settings() {
  const session = await getSession();
  if (!session.operator) redirect("/login");
  return (
    <main style={{ padding: 24 }}>
      <h1>Settings</h1>
      <p>
        Trading mode: <code>{process.env.TRADING_MODE ?? "paper"}</code>
      </p>
      <hr />
      <h2>Kill switch</h2>
      <KillSwitch />
    </main>
  );
}
