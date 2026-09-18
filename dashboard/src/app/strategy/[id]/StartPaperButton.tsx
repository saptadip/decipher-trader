"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function StartPaperButton({ id }: { id: number }) {
  const router = useRouter();
  const [err, setErr] = useState<string | null>(null);
  async function onClick() {
    const resp = await fetch(`/api/control/strategies/${id}/start_paper`, { method: "POST" });
    if (resp.ok) router.refresh();
    else setErr(await resp.text());
  }
  return (
    <>
      <button
        onClick={onClick}
        style={{
          background: "#f59e0b",
          color: "white",
          padding: "8px 14px",
          border: 0,
          borderRadius: 6,
        }}
      >
        Start paper trading
      </button>
      {err ? <pre style={{ color: "red" }}>{err}</pre> : null}
    </>
  );
}
