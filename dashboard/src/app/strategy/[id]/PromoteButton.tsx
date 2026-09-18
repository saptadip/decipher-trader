"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function PromoteButton({ id }: { id: number }) {
  const router = useRouter();
  const [err, setErr] = useState<string | null>(null);
  async function onClick() {
    const resp = await fetch(`/api/control/strategies/${id}/promote`, { method: "POST" });
    if (resp.ok) router.refresh();
    else setErr(await resp.text());
  }
  return (
    <>
      <button
        onClick={onClick}
        style={{
          background: "#10b981",
          color: "white",
          padding: "8px 14px",
          border: 0,
          borderRadius: 6,
        }}
      >
        Promote to live
      </button>
      {err ? <pre style={{ color: "red" }}>{err}</pre> : null}
    </>
  );
}
