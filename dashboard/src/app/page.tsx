import { getSession } from "@/lib/session";
import { controlPlane } from "@/lib/controlPlane";
import { redirect } from "next/navigation";
import StatusBadge from "@/components/StatusBadge";
import Link from "next/link";

interface Row {
  id: number;
  name: string;
  status: string;
  capital_weight: number;
}

export default async function Home() {
  const session = await getSession();
  if (!session.operator) redirect("/login");

  const resp = await controlPlane.get("/strategies");
  const rows: Row[] = resp.ok ? await resp.json() : [];

  return (
    <main style={{ padding: 24 }}>
      <h1>Strategies</h1>
      <table style={{ borderCollapse: "collapse" }}>
        <thead>
          <tr>
            <th align="left">Name</th>
            <th align="left">Status</th>
            <th align="right">Weight</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td style={{ padding: 6 }}>{r.name}</td>
              <td style={{ padding: 6 }}>
                <StatusBadge status={r.status} />
              </td>
              <td style={{ padding: 6 }} align="right">
                {r.capital_weight.toFixed(2)}
              </td>
              <td style={{ padding: 6 }}>
                <Link href={`/strategy/${r.id}`}>details</Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
