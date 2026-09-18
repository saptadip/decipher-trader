"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function KillSwitch() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  async function onClick() {
    if (!confirm("Demote EVERY live strategy back to paper. Continue?")) return;
    setBusy(true);
    const resp = await fetch("/api/control/kill_all", { method: "POST" });
    setBusy(false);
    if (resp.ok) {
      const body = await resp.json();
      setMsg(`Demoted: ${body.demoted.join(", ") || "none"}`);
      router.refresh();
    } else {
      setMsg(await resp.text());
    }
  }
  return (
    <>
      <button
        onClick={onClick}
        disabled={busy}
        style={{
          background: "#b91c1c",
          color: "white",
          padding: "10px 16px",
          border: 0,
          borderRadius: 6,
          fontWeight: 600,
        }}
      >
        {busy ? "Working…" : "KILL ALL LIVE STRATEGIES"}
      </button>
      {msg ? <p>{msg}</p> : null}
    </>
  );
}
