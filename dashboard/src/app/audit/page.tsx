import { getSession } from "@/lib/session";
import { controlPlane } from "@/lib/controlPlane";
import { redirect } from "next/navigation";

interface Entry {
  id: number;
  ts: string;
  actor: string;
  action: string;
  payload_json: string;
}

export default async function Audit() {
  const session = await getSession();
  if (!session.operator) redirect("/login");
  const resp = await controlPlane.get("/audit?limit=200");
  const entries: Entry[] = resp.ok ? await resp.json() : [];
  return (
    <main style={{ padding: 24 }}>
      <h1>Audit log</h1>
      <table style={{ borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr>
            <th align="left">Time</th>
            <th align="left">Actor</th>
            <th align="left">Action</th>
            <th align="left">Payload</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.id}>
              <td style={{ padding: 4 }}>{e.ts}</td>
              <td style={{ padding: 4 }}>{e.actor}</td>
              <td style={{ padding: 4 }}>{e.action}</td>
              <td
                style={{
                  padding: 4,
                  maxWidth: 400,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {e.payload_json}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
