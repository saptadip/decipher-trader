import { getSession } from "@/lib/session";
import { controlPlane } from "@/lib/controlPlane";
import { redirect } from "next/navigation";
import StatusBadge from "@/components/StatusBadge";
import PnLSparkline from "@/components/PnLSparkline";
import PromoteButton from "./PromoteButton";
import DemoteButton from "./DemoteButton";
import StartPaperButton from "./StartPaperButton";

export default async function Detail({ params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session.operator) redirect("/login");
  const { id } = await params;

  const [sResp, mResp] = await Promise.all([
    controlPlane.get(`/strategies?status=draft,backtest,paper,live,retired`),
    controlPlane.get(`/metrics/${id}`),
  ]);
  const rows = (await sResp.json()) as Array<{
    id: number;
    name: string;
    status: string;
    paper_started_at: string | null;
    promoted_at: string | null;
  }>;
  const row = rows.find((r) => r.id === Number(id));
  const metrics = (await mResp.json()) as Array<{ pnl: number }>;

  if (!row) return <main style={{ padding: 24 }}>Not found.</main>;

  return (
    <main style={{ padding: 24 }}>
      <h1>{row.name}</h1>
      <p>
        Status: <StatusBadge status={row.status} />
      </p>
      <p>Paper started: {row.paper_started_at ?? "—"}</p>
      <p>Promoted at: {row.promoted_at ?? "—"}</p>
      <p>PnL trend:</p>
      <PnLSparkline series={metrics.map((m) => m.pnl)} />
      <div style={{ marginTop: 24 }}>
        {row.status === "draft" ? <StartPaperButton id={row.id} /> : null}
        {row.status === "paper" ? <PromoteButton id={row.id} /> : null}
        {row.status === "live" ? <DemoteButton id={row.id} /> : null}
      </div>
    </main>
  );
}
