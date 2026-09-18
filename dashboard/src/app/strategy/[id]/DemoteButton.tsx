"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function DemoteButton({ id }: { id: number }) {
  const router = useRouter();
  const [err, setErr] = useState<string | null>(null);
  async function onClick() {
    if (!confirm("Demote this strategy from live back to paper?")) return;
    const resp = await fetch(`/api/control/strategies/${id}/demote`, { method: "POST" });
    if (resp.ok) router.refresh();
    else setErr(await resp.text());
  }
  return (
    <>
      <button
        onClick={onClick}
        style={{
          background: "#ef4444",
          color: "white",
          padding: "8px 14px",
          border: 0,
          borderRadius: 6,
        }}
      >
        Demote to paper
      </button>
      {err ? <pre style={{ color: "red" }}>{err}</pre> : null}
    </>
  );
}
